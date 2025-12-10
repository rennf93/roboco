"""
API Routes

All FastAPI route modules.
"""

from roboco.api.routes import (
    channels,
    health,
    journals,
    messages,
    notifications,
    optimal,
    sessions,
    stream,
    tasks,
    kanban,
    dashboard,
)

__all__ = [
    # Phase 1-2
    "channels",
    "health",
    "messages",
    "notifications",
    "sessions",
    "stream",
    # Phase 3
    "optimal",
    "journals",
    # Phase 5
    "tasks",
    "kanban",
    "dashboard",
]
