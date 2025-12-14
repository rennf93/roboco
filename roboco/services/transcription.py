"""
Transcription Service

Processes raw LLM stream output from agents and buffers it for extraction.
This service sits between the WebSocket stream and the extraction pipeline.

Flow:
1. Agent produces LLM output -> streamed via WebSocket
2. TranscriptionService buffers chunks
3. When buffer is ready (sentence complete, pause detected, etc.)
4. ExtractionService processes buffer into ExtractedMessages
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Callable
from datetime import timedelta
from uuid import UUID

import structlog

from roboco.models.message import RawStream
from roboco.models.transcription import StreamBuffer, TranscriptionConfig

logger = structlog.get_logger()


class TranscriptionService:
    """
    Service for transcribing and buffering agent LLM streams.

    Responsibilities:
    - Receive raw stream chunks from agents
    - Buffer chunks into coherent segments
    - Detect segment boundaries (sentences, pauses, completions)
    - Yield ready segments for extraction

    Usage:
        service = TranscriptionService()
        await service.start()

        # Feed chunks from WebSocket
        await service.process_chunk(raw_stream)

        # Get ready segments
        async for segment in service.get_ready_segments():
            extracted = await extraction_service.extract(segment)
    """

    def __init__(self, config: TranscriptionConfig | None = None) -> None:
        self.config = config or TranscriptionConfig()

        # agent_id -> list of buffers (can have multiple concurrent sessions)
        self._buffers: dict[UUID, dict[UUID, StreamBuffer]] = {}

        # Callbacks for when segments are ready
        self._segment_callbacks: list[Callable[[StreamBuffer], None]] = []

        # Background task for periodic flushing
        self._flush_task: asyncio.Task | None = None
        self._running = False

        self.log = logger.bind(component="transcription")

    async def start(self) -> None:
        """Start the transcription service."""
        if self._running:
            return

        self._running = True
        self._flush_task = asyncio.create_task(self._periodic_flush())
        self.log.info("Transcription service started")

    async def stop(self) -> None:
        """Stop the transcription service."""
        if not self._running:
            return

        self._running = False

        if self._flush_task:
            self._flush_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._flush_task

        # Flush all remaining buffers
        await self._flush_all()

        self.log.info("Transcription service stopped")

    def get_buffer(
        self,
        agent_id: UUID,
        session_id: UUID,
        channel_id: UUID,
    ) -> StreamBuffer:
        """Get or create a buffer for an agent session."""
        if agent_id not in self._buffers:
            self._buffers[agent_id] = {}

        agent_buffers = self._buffers[agent_id]

        if session_id not in agent_buffers:
            # Create new buffer
            buffer = StreamBuffer(
                agent_id=agent_id,
                channel_id=channel_id,
                session_id=session_id,
            )
            agent_buffers[session_id] = buffer

            self.log.debug(
                "Created buffer",
                agent_id=str(agent_id),
                session_id=str(session_id),
            )

        return agent_buffers[session_id]

    async def process_chunk(self, raw_stream: RawStream) -> StreamBuffer | None:
        """
        Process a raw stream chunk.

        Returns the buffer if it's ready for extraction.
        """
        buffer = self.get_buffer(
            agent_id=raw_stream.agent_id,
            session_id=raw_stream.connection_id,  # Use connection_id as session
            channel_id=raw_stream.channel_id,
        )

        # Append chunk
        buffer.append(raw_stream.chunk)

        self.log.debug(
            "Processed chunk",
            agent_id=str(raw_stream.agent_id),
            chunk_size=len(raw_stream.chunk),
            buffer_size=buffer.char_count,
        )

        # Check if ready
        if buffer.is_ready_for_extraction(
            min_chars=self.config.min_chars_for_extraction,
            idle_threshold=timedelta(seconds=self.config.idle_threshold_seconds),
            max_chars=self.config.max_chars_before_flush,
        ):
            return buffer

        return None

    async def process_stream_complete(
        self,
        agent_id: UUID,
        session_id: UUID,
    ) -> StreamBuffer | None:
        """
        Mark a stream as complete and return the buffer.

        Called when an agent finishes generating (end of response).
        """
        if agent_id not in self._buffers:
            return None

        agent_buffers = self._buffers[agent_id]

        if session_id not in agent_buffers:
            return None

        buffer = agent_buffers[session_id]
        buffer.is_complete = True

        if buffer.char_count > 0:
            return buffer

        return None

    async def flush_buffer(
        self,
        agent_id: UUID,
        session_id: UUID,
    ) -> str | None:
        """
        Flush a buffer and return its content.

        Removes the buffer after flushing.
        """
        if agent_id not in self._buffers:
            return None

        agent_buffers = self._buffers[agent_id]

        if session_id not in agent_buffers:
            return None

        buffer = agent_buffers.pop(session_id)
        content = buffer.clear()

        self.log.debug(
            "Flushed buffer",
            agent_id=str(agent_id),
            session_id=str(session_id),
            content_length=len(content),
        )

        return content

    async def get_ready_buffers(self) -> AsyncIterator[StreamBuffer]:
        """
        Iterate over all buffers that are ready for extraction.

        Does not remove buffers; caller should flush after extraction.
        """
        for _agent_id, agent_buffers in list(self._buffers.items()):
            for _session_id, buffer in list(agent_buffers.items()):
                if buffer.is_ready_for_extraction(
                    min_chars=self.config.min_chars_for_extraction,
                    idle_threshold=timedelta(
                        seconds=self.config.idle_threshold_seconds
                    ),
                    max_chars=self.config.max_chars_before_flush,
                ):
                    yield buffer

    async def _periodic_flush(self) -> None:
        """Background task to periodically check and flush ready buffers."""
        while self._running:
            try:
                await asyncio.sleep(self.config.flush_interval_seconds)

                async for buffer in self.get_ready_buffers():
                    # Notify callbacks
                    for callback in self._segment_callbacks:
                        try:
                            callback(buffer)
                        except Exception as e:
                            self.log.error(
                                "Callback error",
                                error=str(e),
                            )

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.log.error("Periodic flush error", error=str(e))

    async def _flush_all(self) -> None:
        """Flush all buffers (called on shutdown)."""
        for agent_id, agent_buffers in list(self._buffers.items()):
            for session_id in list(agent_buffers.keys()):
                await self.flush_buffer(agent_id, session_id)

        self._buffers.clear()

    def register_callback(
        self,
        callback: Callable[[StreamBuffer], None],
    ) -> None:
        """Register a callback for when segments are ready."""
        self._segment_callbacks.append(callback)

    def get_stats(self) -> dict:
        """Get service statistics."""
        total_buffers = sum(
            len(agent_buffers) for agent_buffers in self._buffers.values()
        )
        total_chars = sum(
            buffer.char_count
            for agent_buffers in self._buffers.values()
            for buffer in agent_buffers.values()
        )

        return {
            "active_agents": len(self._buffers),
            "total_buffers": total_buffers,
            "total_buffered_chars": total_chars,
            "running": self._running,
        }
