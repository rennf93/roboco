"""
Event System for RoboCo

Handles workflow triggers and event-driven communication between components.
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

__all__ = [
    "Event",
    "EventBus",
    "EventType",
    "get_event_bus",
    "get_event_context",
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
