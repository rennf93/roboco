"""
Event Bus

Redis Streams-based event system for durable workflow triggers.

This module re-exports StreamEventBus as EventBus for backward compatibility.
All events are now persisted using Redis Streams with consumer groups for
guaranteed at-least-once delivery.
"""

from collections.abc import Callable, Coroutine
from typing import Any

from roboco.events.stream_bus import (
    StreamEventBus,
    get_stream_event_bus,
    init_stream_event_bus,
)
from roboco.models.events import Event, EventType

# Type for event handlers (re-export for backward compatibility)
EventHandler = Callable[[Event], Coroutine[Any, Any, None]]

# Re-export StreamEventBus as EventBus for backward compatibility
EventBus = StreamEventBus

__all__ = [
    "Event",
    "EventBus",
    "EventHandler",
    "EventType",
    "get_event_bus",
    "init_event_bus",
]


def get_event_bus() -> StreamEventBus:
    """Get or create the global event bus instance."""
    return get_stream_event_bus()


async def init_event_bus(
    consumer_name: str | None = None,
    recover_pending: bool = True,
) -> StreamEventBus:
    """
    Initialize and start the event bus.

    Args:
        consumer_name: Unique name for this consumer instance
        recover_pending: Whether to recover unacknowledged messages on startup
    """
    return await init_stream_event_bus(
        consumer_name=consumer_name,
        recover_pending=recover_pending,
    )
