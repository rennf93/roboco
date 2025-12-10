"""
Event Bus

Redis-based pub/sub event system for workflow triggers.
"""

import asyncio
import contextlib
import json
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

import redis.asyncio as redis
import structlog

from roboco.config import settings

logger = structlog.get_logger()


class EventType(str, Enum):
    """Types of events in the system."""

    # Task lifecycle events
    TASK_CREATED = "task.created"
    TASK_CLAIMED = "task.claimed"
    TASK_STARTED = "task.started"
    TASK_BLOCKED = "task.blocked"
    TASK_UNBLOCKED = "task.unblocked"
    TASK_PAUSED = "task.paused"
    TASK_RESUMED = "task.resumed"
    TASK_VERIFYING = "task.verifying"
    TASK_AWAITING_QA = "task.awaiting_qa"
    TASK_QA_PASSED = "task.qa_passed"
    TASK_QA_FAILED = "task.qa_failed"
    TASK_AWAITING_DOCS = "task.awaiting_docs"
    TASK_COMPLETED = "task.completed"
    TASK_CANCELLED = "task.cancelled"

    # Session events
    SESSION_CREATED = "session.created"
    SESSION_CLOSED = "session.closed"
    SESSION_TIMEOUT = "session.timeout"

    # Handoff events
    HANDOFF_CREATED = "handoff.created"
    HANDOFF_ACCEPTED = "handoff.accepted"

    # Agent events
    AGENT_SPAWNED = "agent.spawned"
    AGENT_STOPPED = "agent.stopped"
    AGENT_WAITING = "agent.waiting"
    AGENT_RESUMED = "agent.resumed"
    AGENT_ERROR = "agent.error"

    # Notification events
    NOTIFICATION_SENT = "notification.sent"
    NOTIFICATION_ACKED = "notification.acked"

    # Blocker events
    BLOCKER_REPORTED = "blocker.reported"
    BLOCKER_RESOLVED = "blocker.resolved"

    # Question events
    QUESTION_ASKED = "question.asked"
    QUESTION_ANSWERED = "question.answered"


@dataclass
class Event:
    """An event in the system."""

    type: EventType
    data: dict[str, Any]
    id: UUID = field(default_factory=uuid4)
    timestamp: datetime = field(default_factory=datetime.utcnow)
    source_agent: str | None = None
    correlation_id: str | None = None  # For tracking related events

    def to_json(self) -> str:
        """Serialize to JSON."""
        return json.dumps(
            {
                "id": str(self.id),
                "type": self.type.value,
                "data": self.data,
                "timestamp": self.timestamp.isoformat(),
                "source_agent": self.source_agent,
                "correlation_id": self.correlation_id,
            }
        )

    @classmethod
    def from_json(cls, json_str: str) -> "Event":
        """Deserialize from JSON."""
        data = json.loads(json_str)
        return cls(
            id=UUID(data["id"]),
            type=EventType(data["type"]),
            data=data["data"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            source_agent=data.get("source_agent"),
            correlation_id=data.get("correlation_id"),
        )


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


# Global event bus instance
_event_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    """Get or create the global event bus instance."""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus


async def init_event_bus() -> EventBus:
    """Initialize and start the event bus."""
    bus = get_event_bus()
    await bus.connect()
    return bus
