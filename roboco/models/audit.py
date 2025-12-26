"""
Audit Models

Data classes for audit logging and security events.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import UUID


class AuditEventType(str, Enum):
    """Types of audit events."""

    # Permission denials
    PERMISSION_DENIED = "permission_denied"
    CHANNEL_ACCESS_DENIED = "channel_access_denied"
    TASK_ACTION_DENIED = "task_action_denied"
    NOTIFICATION_DENIED = "notification_denied"
    STATE_TRANSITION_DENIED = "state_transition_denied"

    # Security events
    UNAUTHORIZED_ACCESS = "unauthorized_access"
    INVALID_TOKEN = "invalid_token"
    RATE_LIMIT_EXCEEDED = "rate_limit_exceeded"

    # Administrative events
    ROLE_CHANGED = "role_changed"
    ACCESS_GRANTED = "access_granted"
    ACCESS_REVOKED = "access_revoked"

    # PM override events
    PM_OVERRIDE = "pm_override"


@dataclass
class PermissionDenialContext:
    """Context for a permission denial audit log."""

    agent_id: UUID | str
    action: str
    resource: str
    resource_id: UUID | str | None = None
    reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class StateTransitionDenialContext:
    """Context for a state transition denial audit log."""

    agent_id: UUID | str
    agent_role: str
    task_id: UUID | str
    current_status: str
    target_status: str
    reason: str | None = None
