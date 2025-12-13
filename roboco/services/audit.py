"""
Audit Service

Logs permission denials and security events for visibility by Auditor and CEO.
All audit logs are persisted and queryable.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any
from uuid import UUID

import structlog


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


logger = structlog.get_logger()


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


class AuditService:
    """
    Service for logging audit events.

    All permission denials and security events are logged here
    for visibility by the Auditor and CEO.

    Usage:
        audit = AuditService()

        # Log a permission denial
        await audit.log_permission_denial(
            agent_id=agent_id,
            action="create_task",
            resource="task",
            reason="Role not permitted",
        )

        # Query audit logs
        logs = await audit.get_recent_denials(limit=50)
    """

    def __init__(self) -> None:
        self.log = logger.bind(service="audit")

    # =========================================================================
    # LOGGING METHODS
    # =========================================================================

    async def log_permission_denial(
        self,
        ctx: PermissionDenialContext,
    ) -> None:
        """
        Log a permission denial.

        This is the primary method for logging when an agent is denied
        permission to perform an action.
        """
        self.log.warning(
            "Permission denied",
            event_type=AuditEventType.PERMISSION_DENIED.value,
            agent_id=str(ctx.agent_id),
            action=ctx.action,
            resource=ctx.resource,
            resource_id=str(ctx.resource_id) if ctx.resource_id else None,
            reason=ctx.reason,
            details=ctx.details,
            timestamp=datetime.now(UTC).isoformat(),
        )

    async def log_channel_access_denial(
        self,
        agent_id: UUID | str,
        channel_slug: str,
        access_type: str,
        reason: str | None = None,
    ) -> None:
        """Log a channel access denial."""
        self.log.warning(
            "Channel access denied",
            event_type=AuditEventType.CHANNEL_ACCESS_DENIED.value,
            agent_id=str(agent_id),
            channel_slug=channel_slug,
            access_type=access_type,
            reason=reason,
            timestamp=datetime.now(UTC).isoformat(),
        )

    async def log_task_action_denial(
        self,
        agent_id: UUID | str,
        agent_role: str,
        task_id: UUID | str,
        action: str,
        reason: str | None = None,
    ) -> None:
        """Log a task action denial."""
        self.log.warning(
            "Task action denied",
            event_type=AuditEventType.TASK_ACTION_DENIED.value,
            agent_id=str(agent_id),
            agent_role=agent_role,
            task_id=str(task_id),
            action=action,
            reason=reason,
            timestamp=datetime.now(UTC).isoformat(),
        )

    async def log_state_transition_denial(
        self,
        ctx: StateTransitionDenialContext,
    ) -> None:
        """Log a state transition denial."""
        self.log.warning(
            "State transition denied",
            event_type=AuditEventType.STATE_TRANSITION_DENIED.value,
            agent_id=str(ctx.agent_id),
            agent_role=ctx.agent_role,
            task_id=str(ctx.task_id),
            current_status=ctx.current_status,
            target_status=ctx.target_status,
            reason=ctx.reason,
            timestamp=datetime.now(UTC).isoformat(),
        )

    async def log_notification_denial(
        self,
        agent_id: UUID | str,
        agent_role: str,
        notification_type: str,
        reason: str | None = None,
    ) -> None:
        """Log a notification permission denial."""
        self.log.warning(
            "Notification permission denied",
            event_type=AuditEventType.NOTIFICATION_DENIED.value,
            agent_id=str(agent_id),
            agent_role=agent_role,
            notification_type=notification_type,
            reason=reason,
            timestamp=datetime.now(UTC).isoformat(),
        )

    async def log_security_event(
        self,
        event_type: AuditEventType,
        agent_id: UUID | str | None,
        description: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Log a general security event."""
        self.log.warning(
            "Security event",
            event_type=event_type.value,
            agent_id=str(agent_id) if agent_id else None,
            description=description,
            details=details,
            timestamp=datetime.now(UTC).isoformat(),
        )


# =============================================================================
# SINGLETON INSTANCE
# =============================================================================


class _AuditServiceHolder:
    """Holder for singleton AuditService instance."""

    instance: AuditService | None = None


def get_audit_service() -> AuditService:
    """Get or create the global audit service instance."""
    if _AuditServiceHolder.instance is None:
        _AuditServiceHolder.instance = AuditService()
    return _AuditServiceHolder.instance
