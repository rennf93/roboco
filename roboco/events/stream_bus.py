"""
Stream Event Bus

Redis Streams-based event system with durable message delivery.
Replaces the pub/sub-based EventBus with persistence and consumer groups.
"""

import asyncio
import contextlib
import os
import socket
from collections.abc import Callable, Coroutine
from typing import Any

import redis.asyncio as redis
import structlog
from redis.exceptions import ResponseError

from roboco.config import settings
from roboco.models.events import Event, EventType

logger = structlog.get_logger()


# Type for event handlers
EventHandler = Callable[[Event], Coroutine[Any, Any, None]]


class StreamEventBus:
    """
    Event bus using Redis Streams for durable message delivery.

    Features:
    - Message persistence (survives Redis restart with AOF)
    - Consumer groups for at-least-once delivery
    - Message acknowledgment after successful processing
    - Automatic stream trimming (configurable retention)
    """

    STREAM_PREFIX = "roboco:stream:"
    DEFAULT_GROUP = "roboco-handlers"
    MAX_STREAM_LENGTH = 10000  # Trim streams to this length

    def __init__(
        self,
        redis_url: str | None = None,
        consumer_name: str | None = None,
        group_name: str | None = None,
    ):
        self.redis_url = redis_url or settings.redis_url
        # Default consumer name is stable across restarts of the same process
        # (host + pid), so pending messages don't get orphaned to a new
        # id(self)-based name every time the orchestrator restarts. Redis
        # consumer groups still auto-reassign via xclaim after idle_time.
        self.consumer_name = consumer_name or (
            f"consumer-{socket.gethostname()}-{os.getpid()}"
        )
        self.group_name = group_name or self.DEFAULT_GROUP
        self._redis: redis.Redis | None = None
        self._handlers: dict[EventType, list[EventHandler]] = {}
        self._running = False
        self._listen_task: asyncio.Task | None = None

    async def connect(self) -> None:
        """Connect to Redis."""
        self._redis = redis.from_url(self.redis_url)
        logger.info("StreamEventBus connected to Redis")

    def is_connected(self) -> bool:
        """Check if the event bus is connected to Redis."""
        return self._redis is not None

    async def disconnect(self) -> None:
        """Disconnect from Redis."""
        self._running = False

        if self._listen_task:
            self._listen_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listen_task

        if self._redis:
            await self._redis.close()

        logger.info("StreamEventBus disconnected")

    def _get_stream_name(self, event_type: EventType) -> str:
        """Get stream name for event type (grouped by prefix)."""
        # Group by event category: task.*, agent.*, notification.*, etc.
        category = event_type.value.split(".")[0]
        return f"{self.STREAM_PREFIX}{category}"

    def _get_all_stream_names(self) -> list[str]:
        """Get all stream names for registered handlers."""
        categories = set()
        for event_type in self._handlers:
            category = event_type.value.split(".")[0]
            categories.add(category)
        return [f"{self.STREAM_PREFIX}{cat}" for cat in categories]

    async def _ensure_consumer_group(self, stream: str) -> None:
        """Ensure consumer group exists for stream."""
        if not self._redis:
            return
        try:
            await self._redis.xgroup_create(
                stream,
                self.group_name,
                id="0",
                mkstream=True,
            )
            logger.debug("Created consumer group", stream=stream, group=self.group_name)
        except ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise
            # Group already exists, that's fine

    def subscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """Subscribe a handler to an event type."""
        if event_type not in self._handlers:
            self._handlers[event_type] = []
        self._handlers[event_type].append(handler)
        logger.debug("Handler subscribed", event_type=event_type.value)

    def unsubscribe(self, event_type: EventType, handler: EventHandler) -> None:
        """Unsubscribe a handler from an event type."""
        if event_type in self._handlers:
            self._handlers[event_type] = [
                h for h in self._handlers[event_type] if h != handler
            ]

    async def publish(self, event: Event) -> str:
        """
        Publish an event to the stream.

        Returns the message ID assigned by Redis.
        """
        if not self._redis:
            raise RuntimeError("StreamEventBus not connected")

        stream = self._get_stream_name(event.type)

        # Add to stream with automatic ID (*) and trim to max length
        raw_message_id = await self._redis.xadd(
            stream,
            {
                "type": event.type.value,
                "data": event.to_json(),
            },
            maxlen=self.MAX_STREAM_LENGTH,
            approximate=True,
        )
        # Convert bytes to str if needed
        message_id = (
            raw_message_id.decode()
            if isinstance(raw_message_id, bytes)
            else str(raw_message_id)
        )

        logger.info(
            "Event published to stream",
            event_type=event.type.value,
            event_id=str(event.id),
            stream=stream,
            message_id=message_id,
            source=event.source_agent,
        )

        return message_id

    async def publish_task_event(
        self,
        event_type: EventType,
        task_id: str,
        agent_id: str | None = None,
        **extra_data: Any,
    ) -> str:
        """Convenience method to publish task-related events."""
        event = Event(
            type=event_type,
            data={"task_id": task_id, **extra_data},
            source_agent=agent_id,
        )
        return await self.publish(event)

    async def start_listening(self) -> None:
        """Start listening for events."""
        if not self._redis:
            raise RuntimeError("StreamEventBus not connected")

        streams = self._get_all_stream_names()
        if not streams:
            logger.warning("No event handlers registered, nothing to subscribe to")
            return

        # Ensure consumer groups exist for all streams
        for stream in streams:
            await self._ensure_consumer_group(stream)

        self._running = True
        self._listen_task = asyncio.create_task(self._listen_loop())
        logger.info("StreamEventBus listening", streams=streams)

    async def _listen_loop(self) -> None:
        """Main event listening loop using XREADGROUP."""
        if not self._redis:
            return

        streams = self._get_all_stream_names()
        # Build stream dict: {stream_name: ">"}  (> = only new messages)
        stream_dict = dict.fromkeys(streams, ">")

        while self._running:
            try:
                await self._listen_tick(stream_dict)
            except asyncio.CancelledError:
                break
            except ResponseError as e:
                if await self._handle_response_error(e, streams):
                    continue
                await asyncio.sleep(1)
            except Exception as e:
                logger.error("Error in stream event loop", error=str(e))
                await asyncio.sleep(1)

    async def _listen_tick(self, stream_dict: dict[str, str]) -> None:
        """Block for one XREADGROUP cycle and dispatch any messages."""
        assert self._redis is not None
        results = await self._redis.xreadgroup(
            self.group_name,
            self.consumer_name,
            stream_dict,
            count=10,
            block=5000,
        )
        if not results:
            return
        for stream_name, messages in results:
            for message_id, data in messages:
                await self._handle_message(stream_name, message_id, data)

    async def _handle_response_error(
        self, exc: ResponseError, streams: list[str]
    ) -> bool:
        """Recover from NOGROUP by rebootstrapping; return True iff recovered."""
        if "NOGROUP" in str(exc):
            logger.warning(
                "Stream consumer group missing; recreating",
                group=self.group_name,
            )
            for stream in streams:
                await self._ensure_consumer_group(stream)
            return True
        logger.error("Error in stream event loop", error=str(exc))
        return False

    @staticmethod
    def _decode_event_data(data: dict) -> str | None:
        """Pull the event payload out of a stream record."""
        event_data = data.get(b"data") or data.get("data")
        if isinstance(event_data, bytes):
            event_data = event_data.decode()
        if not event_data or not isinstance(event_data, str):
            return None
        return event_data

    @staticmethod
    def _check_handler_results(event: Event, handlers: list, results: list) -> bool:
        """Log handler errors; return True only when every handler succeeded."""
        all_succeeded = True
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                all_succeeded = False
                logger.error(
                    "Event handler error",
                    event_type=event.type.value,
                    handler=handlers[i].__name__,
                    error=str(result),
                )
        return all_succeeded

    async def _dispatch_event(self, event: Event) -> bool:
        """Run all handlers for an event; return True if all succeeded."""
        handlers = self._handlers.get(event.type, [])
        if not handlers:
            return True

        logger.debug(
            "Handling event from stream",
            event_type=event.type.value,
            handler_count=len(handlers),
        )
        tasks = [handler(event) for handler in handlers]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return self._check_handler_results(event, handlers, results)

    async def _handle_message(
        self,
        stream: str,
        message_id: str,
        data: dict,
    ) -> None:
        """Handle an incoming message and ACK on success."""
        if not self._redis:
            return

        try:
            event_data = self._decode_event_data(data)
            if event_data is None:
                logger.error("Invalid event data", message_id=message_id)
                await self._redis.xack(stream, self.group_name, message_id)
                return

            event = Event.from_json(event_data)
            all_succeeded = await self._dispatch_event(event)

            # ACK the message if all handlers succeeded
            # If any failed, message stays pending and can be reclaimed later
            if all_succeeded:
                await self._redis.xack(stream, self.group_name, message_id)
                logger.debug("Message acknowledged", message_id=message_id)
            else:
                logger.warning(
                    "Message not acknowledged due to handler errors",
                    message_id=message_id,
                )

        except Exception as e:
            logger.error(
                "Failed to handle stream message",
                error=str(e),
                message_id=message_id,
            )

    async def _claim_and_handle(
        self, stream: str, msg_id: str, idle_time_ms: int
    ) -> int:
        """Claim a single idle message and process it; return count recovered."""
        if self._redis is None:
            raise RuntimeError("Invariant: self._redis must be set — guarded by caller")
        claimed = await self._redis.xclaim(
            stream,
            self.group_name,
            self.consumer_name,
            min_idle_time=idle_time_ms,
            message_ids=[msg_id],
        )
        if not claimed:
            return 0
        for claim_id, data in claimed:
            await self._handle_message(stream, claim_id, data)
        return 1

    async def _recover_stream(self, stream: str, idle_time_ms: int) -> int:
        """Recover idle pending messages from a single stream."""
        if self._redis is None:
            raise RuntimeError("Invariant: self._redis must be set — guarded by caller")
        pending = await self._redis.xpending(stream, self.group_name)
        if not pending or pending["pending"] == 0:
            return 0

        pending_details = await self._redis.xpending_range(
            stream,
            self.group_name,
            min="-",
            max="+",
            count=100,
        )

        recovered = 0
        for msg in pending_details:
            if msg["time_since_delivered"] >= idle_time_ms:
                recovered += await self._claim_and_handle(
                    stream, msg["message_id"], idle_time_ms
                )
        return recovered

    async def recover_pending(self, idle_time_ms: int = 60000) -> int:
        """
        Recover pending messages that weren't acknowledged.

        Useful for startup to process messages from crashed consumers.

        Args:
            idle_time_ms: Only recover messages idle for this long (default 1 minute)

        Returns:
            Number of messages recovered
        """
        if not self._redis:
            return 0

        recovered = 0
        for stream in self._get_all_stream_names():
            try:
                recovered += await self._recover_stream(stream, idle_time_ms)
            except Exception as e:
                logger.error(
                    "Error recovering pending messages",
                    stream=stream,
                    error=str(e),
                )

        if recovered:
            logger.info("Recovered pending messages", count=recovered)

        return recovered


# =============================================================================
# SINGLETON ACCESS
# =============================================================================


class _StreamEventBusHolder:
    """Holder for singleton StreamEventBus instance."""

    instance: StreamEventBus | None = None


def get_stream_event_bus() -> StreamEventBus:
    """Get or create the global stream event bus instance."""
    if _StreamEventBusHolder.instance is None:
        _StreamEventBusHolder.instance = StreamEventBus()
    return _StreamEventBusHolder.instance


async def init_stream_event_bus(
    consumer_name: str | None = None,
    recover_pending: bool = True,
) -> StreamEventBus:
    """
    Initialize and start the stream event bus.

    Args:
        consumer_name: Unique name for this consumer instance
        recover_pending: Whether to recover unacknowledged messages on startup
    """
    bus = get_stream_event_bus()
    if consumer_name:
        bus.consumer_name = consumer_name
    await bus.connect()

    if recover_pending:
        await bus.recover_pending()

    return bus
