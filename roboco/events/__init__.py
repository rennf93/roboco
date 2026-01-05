"""
Event System for RoboCo

Handles workflow triggers and event-driven communication between components.

Uses Redis Streams for durable message delivery with consumer groups.
All events are persisted and delivered with at-least-once semantics.
"""

from roboco.events.bus import Event, EventBus, EventType, get_event_bus, init_event_bus
from roboco.events.handlers import (
    get_event_context,
    handle_blocker_resolved,
    handle_handoff_created,
    handle_qa_result,
    handle_question_answered,
    handle_session_boundary,
    handle_task_status_change,
    register_default_handlers,
    set_event_context,
)
from roboco.events.stream_bus import StreamEventBus, get_stream_event_bus

__all__ = [
    "Event",
    "EventBus",
    "EventType",
    "StreamEventBus",
    "get_event_bus",
    "get_event_context",
    "get_stream_event_bus",
    "handle_blocker_resolved",
    "handle_handoff_created",
    "handle_qa_result",
    "handle_question_answered",
    "handle_session_boundary",
    "handle_task_status_change",
    "init_event_bus",
    "register_default_handlers",
    "set_event_context",
]
