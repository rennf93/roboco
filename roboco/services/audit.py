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

        # Log a task action denial
        await audit.log_task_action_denial(
            agent_id=agent_id,
            agent_role="developer",
            task_id=task_id,
            action="claim",
            reason="Role not permitted",
        )

        # Query audit logs
        events = await audit.get_recent_events(limit=50)
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

    async def log_task_action_denial(
        self,
        agent_id: str | UUID,
        agent_role: str,
        task_id: str | UUID,
        action: str,
        reason: str | None = None,
    ) -> None:
        """Log a task action denial.

        The persisted ``agent_role`` is the actor's actual role read from
        ``agents.role`` at write time, not the caller-supplied param.
        Pre-fix the supplied param could disagree with the DB (verb's
        expected role vs caller's actual role); the DB is authoritative.

        A non-UUID ``task_id`` sentinel (e.g. ``"N/A"``) is preserved in
        ``details["target_id_raw"]`` rather than dropped silently to a NULL
        target_id indistinguishable from any other NULL-target denial. For a
        ``create`` denied before any task row exists, prefer
        :meth:`log_task_creation_denial`, which records the attempted payload
        under a distinct ``task_creation`` target_type.
        """
        actual_role = await self._resolve_actor_role_from_db(agent_id) or agent_role
        details: dict[str, Any] = {
            "agent_role": actual_role,
            "action": action,
            "reason": reason,
        }
        coerced = _coerce_uuid(task_id)
        if coerced is None and task_id is not None:
            details["target_id_raw"] = str(task_id)
        self.log.warning(
            "Task action denied",
            event_type=AuditEventType.TASK_ACTION_DENIED.value,
            agent_id=str(agent_id),
            agent_role=actual_role,
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
                target_id=coerced,
                severity="warning",
                details=details,
            )
        )

    async def log_task_creation_denial(
        self,
        agent_id: str | UUID,
        agent_role: str,
        action: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Log a denial of task creation (no task row exists yet).

        A ``create`` denial has no ``task_id``; a ``"N/A"`` placeholder would
        coerce to a NULL ``target_id`` indistinguishable from any other
        NULL-target denial, leaving the role-escalation attempt unattributable.
        The attempted payload is recorded in ``details`` under a distinct
        ``task_creation`` target_type so the Auditor can see what was tried.
        """
        actual_role = await self._resolve_actor_role_from_db(agent_id) or agent_role
        merged: dict[str, Any] = {
            "agent_role": actual_role,
            "action": action,
        }
        if details:
            merged.update(details)
        self.log.warning(
            "Task creation denied",
            event_type=AuditEventType.TASK_ACTION_DENIED.value,
            agent_id=str(agent_id),
            agent_role=actual_role,
            action=action,
            timestamp=datetime.now(UTC).isoformat(),
        )
        await self._persist(
            _AuditEvent(
                event_type=AuditEventType.TASK_ACTION_DENIED.value,
                agent_id=agent_id,
                target_type="task_creation",
                target_id=None,
                severity="warning",
                details=merged,
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

    async def log_event(
        self,
        *,
        event_type: str,
        agent_id: str | UUID | None = None,
        task_id: str | UUID | None = None,
        details: dict[str, Any] | None = None,
        severity: str = "warning",
    ) -> None:
        """Log a generic audit event.

        Free-form ``event_type`` (e.g. ``"gateway.rejected"``) lets callers
        emit categorized signals without extending ``AuditEventType`` for
        every new surface. The Choreographer uses this for gate-rejection
        forensics; other layers can adopt the same shape.
        """
        self.log.warning(
            "Audit event",
            event_type=event_type,
            agent_id=str(agent_id) if agent_id else None,
            task_id=str(task_id) if task_id else None,
            details=details or {},
            timestamp=datetime.now(UTC).isoformat(),
        )
        await self._persist(
            _AuditEvent(
                event_type=event_type,
                agent_id=agent_id,
                target_type="task" if task_id else None,
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

        Resolves ``agent_slug`` to its UUID so ``audit_log.agent_id`` is a
        real FK to ``agents.id`` (Auditor + CEO queries can join on it).
        The slug is also kept in ``details`` for redundancy and for the
        case where an unknown slug fails to resolve (best-effort: the row
        is still written with ``agent_id=NULL`` rather than dropped).
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
        resolved_agent_id = await self._resolve_agent_id_by_slug(agent_slug)
        await self._persist(
            _AuditEvent(
                event_type=event_type,
                agent_id=resolved_agent_id,
                target_type="task" if task_id else "agent",
                target_id=task_id,
                severity=severity,
                details=payload,
            )
        )

    async def _resolve_actor_role_from_db(
        self, agent_id: str | UUID | None
    ) -> str | None:
        """Read the actor's actual role from agents.role at write time.

        Pre-2026-05-08, every denial-log call took an `agent_role` string
        from the caller. The trace caught a row where actor=main-pm but
        agent_role=cell_pm — caller had passed the verb's *expected*
        role rather than the actor's actual role. This helper looks up
        the truth at write time. Best-effort: returns None on any
        failure so the caller's supplied role can be used as a fallback.
        """
        actor_uuid = _coerce_uuid(agent_id)
        if actor_uuid is None:
            return None
        try:
            from sqlalchemy import select

            from roboco.db.base import get_session_factory
            from roboco.db.tables import AgentTable

            session_factory = get_session_factory()
            async with session_factory() as db:
                result = await db.execute(
                    select(AgentTable.role).where(AgentTable.id == actor_uuid)
                )
                value = result.scalar_one_or_none()
                if value is None:
                    return None
                # AgentTable.role is an enum; .value gives the canonical string
                # (e.g. "main_pm"). Defensive: handle plain str too.
                return getattr(value, "value", None) or str(value)
        except Exception as e:
            self.log.debug(
                "DB actor-role lookup failed for audit row",
                agent_id=str(agent_id),
                error=str(e),
            )
            return None

    async def _resolve_agent_id_by_slug(self, agent_slug: str) -> UUID | None:
        """Resolve an agent slug to its UUID for ``audit_log.agent_id``.

        Tries the static ``AGENT_UUIDS`` map first (the 18 seeded agents);
        falls back to a DB lookup for agents not in the seed set (test
        agents, future-added agents). Best-effort: returns None on any
        failure so the audit write still succeeds with ``agent_id=NULL``
        — observability must never block the operation being observed.
        """
        # Fast path: the 18 seeded agents are in the static map. No DB hit.
        try:
            from roboco.seeds.initial_data import AGENT_UUIDS

            seeded = AGENT_UUIDS.get(agent_slug)
            if seeded is not None:
                return UUID(seeded)
        except Exception as e:
            self.log.debug(
                "Static AGENT_UUIDS lookup failed; falling back to DB",
                agent_slug=agent_slug,
                error=str(e),
            )

        # Slow path: DB lookup for agents added at runtime.
        try:
            from sqlalchemy import select

            from roboco.db.base import get_session_factory
            from roboco.db.tables import AgentTable

            session_factory = get_session_factory()
            async with session_factory() as db:
                result = await db.execute(
                    select(AgentTable.id).where(AgentTable.slug == agent_slug)
                )
                value = result.scalar_one_or_none()
                if value is None:
                    return None
                return UUID(str(value))
        except Exception as e:
            self.log.debug(
                "DB slug-to-UUID resolution failed for audit row",
                agent_slug=agent_slug,
                error=str(e),
            )
            return None

    # =========================================================================
    # QUERY METHODS
    # =========================================================================

    async def has_recent_tracing_gap(
        self,
        *,
        agent_id: UUID,
        task_id: UUID,
        since: datetime,
    ) -> bool:
        """Has this (agent, task) emitted a ``gateway.rejected`` ``tracing_gap``?

        Used by the orchestrator's PM respawn circuit breaker to tell
        rule-following retries (agent hit a claim-time gate, returned a
        ``tracing_gap`` envelope, and is being re-spawned to call the
        prerequisite verb) apart from genuine no-progress hangs. The
        former must reset the strike count; the latter must increment it.

        Returns ``True`` if at least one ``audit_log`` row exists where:

        * ``event_type == "gateway.rejected"``
        * ``agent_id`` matches
        * ``target_id`` matches the task UUID
        * ``timestamp >= since``
        * ``details->>'reason' == 'tracing_gap'``

        Best-effort: if the underlying query raises, the caller is expected
        to fall back to the legacy strike behavior — observability must
        never block the orchestrator.
        """
        from sqlalchemy import select

        from roboco.db.base import get_session_factory
        from roboco.db.tables import AuditLogTable

        session_factory = get_session_factory()
        async with session_factory() as db:
            query = (
                select(AuditLogTable.id)
                .where(AuditLogTable.event_type == "gateway.rejected")
                .where(AuditLogTable.agent_id == agent_id)
                .where(AuditLogTable.target_id == task_id)
                .where(AuditLogTable.timestamp >= since)
                .where(AuditLogTable.details["reason"].astext == "tracing_gap")
                .limit(1)
            )
            result = await db.execute(query)
            return result.scalar_one_or_none() is not None

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
