"""
Audit Service

Logs permission denials and security events for visibility by Auditor and CEO.
All audit logs are persisted and queryable.
"""

from datetime import UTC, datetime
from typing import ClassVar
from uuid import UUID

from roboco.models.audit import (
    AuditEventType,
    PermissionDenialContext,
    StateTransitionDenialContext,
)
from roboco.services.base import SingletonService


class AuditService(SingletonService):
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

    service_name: ClassVar[str] = "audit"

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
        agent_id: str,
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
        agent_id: str | UUID,
        agent_role: str,
        task_id: str | UUID,
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
        agent_id: str,
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
        agent_id: str | None,
        description: str,
        details: dict | None = None,
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

    async def log_pm_override(
        self,
        agent_id: str | UUID,
        task_id: str | UUID,
        action: str,
        justification: str,
        cancelled_subtask_ids: list[str] | None = None,
    ) -> None:
        """Log when a PM uses an override capability.

        PM overrides are legitimate but need auditing - e.g., completing
        a task despite cancelled subtasks when PM judges work is done.
        """
        self.log.info(
            "PM override used",
            event_type=AuditEventType.PM_OVERRIDE.value,
            agent_id=str(agent_id),
            task_id=str(task_id),
            action=action,
            justification=justification,
            cancelled_subtask_ids=cancelled_subtask_ids,
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
