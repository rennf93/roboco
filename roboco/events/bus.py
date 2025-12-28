"""
Event Bus

Redis-based pub/sub event system for workflow triggers.
"""

import asyncio
import contextlib
from collections.abc import Callable, Coroutine
from typing import Any

import redis.asyncio as redis
import structlog

from roboco.config import settings
from roboco.models.events import Event, EventType

logger = structlog.get_logger()


# Type for event handlers
EventHandler = Callable[[Event], Coroutine[Any, Any, None]]


class EventBus:
    """
    Event bus for publishing and subscribing to events.

    Uses Redis pub/sub for distributed event handling.
    """

    CHANNEL_PREFIX = "roboco:events:"

    def __init__(self, redis_url: str | None = None):
        self.redis_url = redis_url or settings.redis_url
        self._redis: redis.Redis | None = None
        self._pubsub: redis.client.PubSub | None = None
        self._handlers: dict[EventType, list[EventHandler]] = {}
        self._running = False
        self._listen_task: asyncio.Task | None = None

    async def connect(self) -> None:
        """Connect to Redis."""
        self._redis = redis.from_url(self.redis_url)
        self._pubsub = self._redis.pubsub()
        logger.info("EventBus connected to Redis")

    async def disconnect(self) -> None:
        """Disconnect from Redis."""
        self._running = False

        if self._listen_task:
            self._listen_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._listen_task

        if self._pubsub:
            await self._pubsub.close()

        if self._redis:
            await self._redis.close()

        logger.info("EventBus disconnected")

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

    async def publish(self, event: Event) -> None:
        """Publish an event."""
        if not self._redis:
            raise RuntimeError("EventBus not connected")

        channel = f"{self.CHANNEL_PREFIX}{event.type.value}"
        await self._redis.publish(channel, event.to_json())

        logger.info(
            "Event published",
            event_type=event.type.value,
            event_id=str(event.id),
            source=event.source_agent,
        )

    async def publish_task_event(
        self,
        event_type: EventType,
        task_id: str,
        agent_id: str | None = None,
        **extra_data: Any,
    ) -> None:
        """Convenience method to publish task-related events."""
        event = Event(
            type=event_type,
            data={"task_id": task_id, **extra_data},
            source_agent=agent_id,
        )
        await self.publish(event)

    async def start_listening(self) -> None:
        """Start listening for events."""
        if not self._pubsub:
            raise RuntimeError("EventBus not connected")

        # Subscribe to all event channels we have handlers for
        channels = [f"{self.CHANNEL_PREFIX}{et.value}" for et in self._handlers]

        if not channels:
            logger.warning("No event handlers registered, nothing to subscribe to")
            return

        await self._pubsub.subscribe(*channels)
        self._running = True
        self._listen_task = asyncio.create_task(self._listen_loop())
        logger.info("EventBus listening", channels=len(channels))

    async def _listen_loop(self) -> None:
        """Main event listening loop."""
        while self._running:
            try:
                if self._pubsub is None:
                    break
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )

                if message and message["type"] == "message":
                    await self._handle_message(message)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in event loop", error=str(e))
                await asyncio.sleep(1)

    async def _handle_message(self, message: dict) -> None:
        """Handle an incoming message."""
        try:
            event = Event.from_json(message["data"])

            handlers = self._handlers.get(event.type, [])
            if not handlers:
                return

            logger.debug(
                "Handling event",
                event_type=event.type.value,
                handler_count=len(handlers),
            )

            # Run all handlers concurrently
            tasks = [handler(event) for handler in handlers]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Log any errors
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(
                        "Event handler error",
                        event_type=event.type.value,
                        handler=handlers[i].__name__,
                        error=str(result),
                    )

        except Exception as e:
            logger.error("Failed to handle message", error=str(e))


class _EventBusHolder:
    """Holder for singleton EventBus instance."""

    instance: EventBus | None = None


def get_event_bus() -> EventBus:
    """Get or create the global event bus instance."""
    if _EventBusHolder.instance is None:
        _EventBusHolder.instance = EventBus()
    return _EventBusHolder.instance


async def init_event_bus() -> EventBus:
    """Initialize and start the event bus."""
    bus = get_event_bus()
    await bus.connect()
    return bus
