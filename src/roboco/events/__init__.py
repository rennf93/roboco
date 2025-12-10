"""
Event System for RoboCo

Handles workflow triggers and event-driven communication between components.
"""

from roboco.events.bus import Event, EventBus, EventType
from roboco.events.handlers import (
    handle_handoff_created,
    handle_qa_result,
    handle_session_boundary,
    handle_task_status_change,
)

__all__ = [
    "Event",
    "EventBus",
    "EventType",
    "handle_handoff_created",
    "handle_qa_result",
    "handle_session_boundary",
    "handle_task_status_change",
]
