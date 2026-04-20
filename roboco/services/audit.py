"""
Audit Service

Logs permission denials and security events for visibility by Auditor and CEO.
All audit logs are written to structured logs AND persisted to the
`audit_log` table so the Auditor agent can query them.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, ClassVar
from uuid import UUID

from roboco.models.audit import (
    AuditEventType,
    PermissionDenialContext,
    StateTransitionDenialContext,
)
from roboco.services.base import SingletonService


@dataclass
class _AuditEvent:
    """Bundled fields for an audit row write.

    Grouped into a dataclass so `_persist` stays under pylint's arg limit
    and so we have one obvious shape for every call site.
    """

    event_type: str
    agent_id: str | UUID | None = None
    target_type: str | None = None
    target_id: str | UUID | None = None
    severity: str = "info"
    details: dict[str, Any] = field(default_factory=dict)


def _coerce_uuid(value: str | UUID | None) -> UUID | None:
    """Best-effort coerce to UUID; returns None for slugs or invalid input."""
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except (ValueError, AttributeError):
        return None


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
    # PERSISTENCE HELPER
    # =========================================================================

    async def _persist(self, event: _AuditEvent) -> None:
        """Write an audit row to the audit_log table.

        Best-effort: failures are logged but never propagate, since audit
        failures must not block the operation being audited. Uses its own
        session so it doesn't depend on the caller holding one.
        """
        try:
            from roboco.db.base import get_session_factory
            from roboco.db.tables import AuditLogTable

            session_factory = get_session_factory()
            async with session_factory() as db:
                row = AuditLogTable(
                    event_type=event.event_type,
                    agent_id=_coerce_uuid(event.agent_id),
                    target_type=event.target_type,
                    target_id=_coerce_uuid(event.target_id),
                    severity=event.severity,
                    details=dict(event.details or {}),
                )
                db.add(row)
                await db.commit()
        except Exception as e:
            self.log.error(
                "Failed to persist audit event",
                event_type=event.event_type,
                error=str(e),
            )

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
        await self._persist(
            _AuditEvent(
                event_type=AuditEventType.PERMISSION_DENIED.value,
                agent_id=ctx.agent_id,
                target_type=ctx.resource,
                target_id=ctx.resource_id,
                severity="warning",
                details={
                    "action": ctx.action,
                    "reason": ctx.reason,
                    **(ctx.details or {}),
                },
            )
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
        await self._persist(
            _AuditEvent(
                event_type=AuditEventType.CHANNEL_ACCESS_DENIED.value,
                agent_id=agent_id,
                target_type="channel",
                severity="warning",
                details={
                    "channel_slug": channel_slug,
                    "access_type": access_type,
                    "reason": reason,
                },
            )
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
        await self._persist(
            _AuditEvent(
                event_type=AuditEventType.TASK_ACTION_DENIED.value,
                agent_id=agent_id,
                target_type="task",
                target_id=task_id,
                severity="warning",
                details={
                    "agent_role": agent_role,
                    "action": action,
                    "reason": reason,
                },
            )
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
        await self._persist(
            _AuditEvent(
                event_type=AuditEventType.STATE_TRANSITION_DENIED.value,
                agent_id=ctx.agent_id,
                target_type="task",
                target_id=ctx.task_id,
                severity="warning",
                details={
                    "agent_role": ctx.agent_role,
                    "current_status": ctx.current_status,
                    "target_status": ctx.target_status,
                    "reason": ctx.reason,
                },
            )
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
        await self._persist(
            _AuditEvent(
                event_type=AuditEventType.NOTIFICATION_DENIED.value,
                agent_id=agent_id,
                target_type="notification",
                severity="warning",
                details={
                    "agent_role": agent_role,
                    "notification_type": notification_type,
                    "reason": reason,
                },
            )
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
        await self._persist(
            _AuditEvent(
                event_type=event_type.value,
                agent_id=agent_id,
                severity="warning",
                details={"description": description, **(details or {})},
            )
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
        await self._persist(
            _AuditEvent(
                event_type=AuditEventType.PM_OVERRIDE.value,
                agent_id=agent_id,
                target_type="task",
                target_id=task_id,
                severity="info",
                details={
                    "action": action,
                    "justification": justification,
                    "cancelled_subtask_ids": cancelled_subtask_ids or [],
                },
            )
        )

    async def log_task_event(
        self,
        *,
        event_type: str,
        task_id: str | UUID,
        agent_id: str | UUID | None = None,
        details: dict[str, Any] | None = None,
        severity: str = "info",
    ) -> None:
        """Log a task-lifecycle event (creation, status transition, etc.).

        Meant to be called from TaskService at every meaningful transition —
        without this the audit_log table only captures denial cases, which
        is why past runs had zero recorded activity.
        """
        self.log.info(
            "Task event",
            event_type=event_type,
            task_id=str(task_id),
            agent_id=str(agent_id) if agent_id else None,
            details=details or {},
            timestamp=datetime.now(UTC).isoformat(),
        )
        await self._persist(
            _AuditEvent(
                event_type=event_type,
                agent_id=agent_id,
                target_type="task",
                target_id=task_id,
                severity=severity,
                details=details or {},
            )
        )

    async def log_agent_event(
        self,
        *,
        event_type: str,
        agent_slug: str,
        task_id: str | UUID | None = None,
        details: dict[str, Any] | None = None,
        severity: str = "info",
    ) -> None:
        """Log an orchestrator-level agent event (spawned, stopped, stranded).

        agent_slug is stored in details because audit_log.agent_id is a UUID
        and the orchestrator only sees slugs at spawn time.
        """
        payload: dict[str, Any] = {"agent_slug": agent_slug}
        if details:
            payload.update(details)

        self.log.info(
            "Agent event",
            event_type=event_type,
            agent_slug=agent_slug,
            task_id=str(task_id) if task_id else None,
            timestamp=datetime.now(UTC).isoformat(),
            **(details or {}),
        )
        await self._persist(
            _AuditEvent(
                event_type=event_type,
                agent_id=None,
                target_type="task" if task_id else "agent",
                target_id=task_id,
                severity=severity,
                details=payload,
            )
        )

    # =========================================================================
    # QUERY METHODS
    # =========================================================================

    async def get_recent_events(
        self,
        limit: int = 50,
        event_type: str | None = None,
        agent_id: UUID | None = None,
        min_severity: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch recent audit events. Intended for the Auditor/CEO queries.

        Returns a list of dicts rather than ORM rows so callers don't need
        to keep a session open.
        """
        from sqlalchemy import select

        from roboco.db.base import get_session_factory
        from roboco.db.tables import AuditLogTable

        session_factory = get_session_factory()
        async with session_factory() as db:
            query = select(AuditLogTable).order_by(AuditLogTable.timestamp.desc())
            if event_type:
                query = query.where(AuditLogTable.event_type == event_type)
            if agent_id:
                query = query.where(AuditLogTable.agent_id == agent_id)
            if min_severity == "warning":
                query = query.where(AuditLogTable.severity.in_(("warning", "error")))
            elif min_severity == "error":
                query = query.where(AuditLogTable.severity == "error")
            query = query.limit(limit)

            result = await db.execute(query)
            rows = list(result.scalars().all())

        return [
            {
                "id": str(r.id),
                "event_type": r.event_type,
                "agent_id": str(r.agent_id) if r.agent_id else None,
                "target_type": r.target_type,
                "target_id": str(r.target_id) if r.target_id else None,
                "severity": r.severity,
                "details": dict(r.details or {}),
                "timestamp": r.timestamp.isoformat() if r.timestamp else None,
            }
            for r in rows
        ]


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
