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
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import structlog

from roboco.models import MessageType
from roboco.models.message import RawStream

logger = structlog.get_logger()


@dataclass
class StreamBuffer:
    """
    Buffer for accumulating stream chunks from an agent.

    Tracks the current buffer content, timing, and metadata.
    """

    agent_id: UUID
    channel_id: UUID
    session_id: UUID
    connection_id: UUID = field(default_factory=uuid4)

    # Buffer content
    content: str = ""
    chunks: list[str] = field(default_factory=list)

    # Timing
    started_at: datetime = field(default_factory=datetime.utcnow)
    last_chunk_at: datetime = field(default_factory=datetime.utcnow)

    # State
    is_complete: bool = False

    def append(self, chunk: str) -> None:
        """Append a chunk to the buffer."""
        self.chunks.append(chunk)
        self.content += chunk
        self.last_chunk_at = datetime.utcnow()

    def clear(self) -> str:
        """Clear buffer and return content."""
        content = self.content
        self.content = ""
        self.chunks.clear()
        self.started_at = datetime.utcnow()
        self.is_complete = False
        return content

    @property
    def age(self) -> timedelta:
        """Time since buffer started."""
        return datetime.utcnow() - self.started_at

    @property
    def idle_time(self) -> timedelta:
        """Time since last chunk."""
        return datetime.utcnow() - self.last_chunk_at

    @property
    def char_count(self) -> int:
        """Current buffer character count."""
        return len(self.content)

    def is_ready_for_extraction(
        self,
        min_chars: int = 50,
        idle_threshold: timedelta = timedelta(seconds=2),
        max_chars: int = 5000,
    ) -> bool:
        """
        Determine if buffer is ready for extraction.

        Ready conditions:
        - Has minimum characters AND idle for threshold
        - Exceeds max characters (force flush)
        - Is marked complete
        - Contains sentence-ending punctuation after min_chars
        """
        if self.is_complete:
            return True

        if self.char_count >= max_chars:
            return True

        if self.char_count >= min_chars:
            # Idle long enough
            if self.idle_time >= idle_threshold:
                return True

            # Contains sentence ending
            if self._has_sentence_ending():
                return True

        return False

    def _has_sentence_ending(self) -> bool:
        """Check if buffer ends with sentence-ending punctuation."""
        stripped = self.content.rstrip()
        if not stripped:
            return False
        return stripped[-1] in ".!?:\n"


@dataclass
class TranscriptionConfig:
    """Configuration for the transcription service."""

    # Buffer thresholds
    min_chars_for_extraction: int = 50
    max_chars_before_flush: int = 5000
    idle_threshold_seconds: float = 2.0

    # Processing
    flush_interval_seconds: float = 1.0

    # Limits
    max_buffers_per_agent: int = 10


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
