"""SecretaryService — the CEO's chief-of-staff acting under command.

Reads company state for the CEO and carries out the CEO's directives. Low-risk
directives (relaying a dictated message) execute immediately; high-impact ones
(charter edits, task control, pitch approval, announcements) are recorded
``pending`` and run only after the CEO confirms — the gate list. Every directive,
executed or queued, is auditable in ``secretary_directives``.

Authority: execution runs with the CEO as actor — the CEO either dictated a
low-risk relay or explicitly confirmed a gated directive. The Secretary never
holds CEO authority itself; this service mediates it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar

from sqlalchemy import select

from roboco.db.tables import SecretaryDirectiveTable
from roboco.foundation.identity import AGENTS
from roboco.models.base import Complexity, TaskNature, TaskStatus, Team
from roboco.models.secretary import GATED_KINDS, DirectiveKind, DirectiveStatus
from roboco.services.base import (
    BaseService,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from roboco.services.company_goals import get_company_goals_service
from roboco.services.messaging import get_messaging_service
from roboco.services.pitch import get_pitch_service
from roboco.services.repositories.query_helpers import get_agent_by_slug
from roboco.services.task import get_task_service
from roboco.utils.converters import InvalidIdentifierError, require_uuid

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import TaskTable
    from roboco.services.task import TaskService

# Sentinel distinguishing "assigned_to not present in the edit payload" from
# an explicit ``None`` (unassign) — a plain ``None`` default would conflate
# the two and silently skip a deliberate unassign.
_UNSET: Any = object()

# Full-detail read is bounded: a long-running task's progress history could
# otherwise blow up the payload. Mirrors the spirit of other list caps in the
# codebase (e.g. get_subtasks' [:500]).
_MAX_PROGRESS_UPDATES = 50

_CEO_ID = AGENTS["ceo"].uuid
_ANNOUNCE_CHANNEL = "announcements"

_REQUIRED_PAYLOAD: dict[DirectiveKind, tuple[str, ...]] = {
    DirectiveKind.RELAY_MESSAGE: ("channel", "text"),
    DirectiveKind.ANNOUNCE: ("text",),
    DirectiveKind.UPDATE_CHARTER: ("charter",),
    DirectiveKind.APPROVE_PITCH: ("pitch_id",),
    DirectiveKind.CONTROL_TASK: ("task_id", "action"),
}


class SecretaryService(BaseService):
    """Read company state + execute/queue the CEO's directives."""

    service_name = "secretary"

    # ------------------------------------------------------------------ #
    # reads (always direct — no authority needed)
    # ------------------------------------------------------------------ #

    async def read_company_state(self) -> dict[str, Any]:
        goals = await get_company_goals_service(self.session).get()
        counts = await get_task_service(self.session).count_by_status()
        pitches = await get_pitch_service(self.session).list_pitches()
        pending = await self.list_directives(DirectiveStatus.PENDING)
        return {
            "goals": goals,
            "task_counts": counts,
            "pending_pitches": [
                {"id": str(p.id), "title": p.title, "slug": p.slug}
                for p in pitches
                if p.status == "proposed"
            ],
            "pending_directives": [self.to_dict(d) for d in pending],
        }

    async def read_task(self, task_id: UUID) -> dict[str, Any]:
        """Full-detail task read — the Secretary's FULL task access.

        Beyond identity/status/description, carries everything the panel's
        full ``TaskResponse`` has that this previously omitted: acceptance
        criteria, plan, notes (dev/qa/auditor/pr-reviewer/doc/quick-context),
        and the PR/branch reference. ``progress_updates`` is bounded to the
        most recent entries (see ``_MAX_PROGRESS_UPDATES``) so a long-running
        task's history can't blow up the payload.
        """
        task: TaskTable | None = await get_task_service(self.session).get(task_id)
        if task is None:
            raise NotFoundError("task", str(task_id))
        return {
            "id": str(task.id),
            "title": task.title,
            "status": str(task.status),
            "team": str(task.team) if task.team else None,
            "assigned_to": str(task.assigned_to) if task.assigned_to else None,
            "description": task.description,
            "acceptance_criteria": list(task.acceptance_criteria or []),
            "priority": task.priority,
            "estimated_complexity": (
                str(task.estimated_complexity) if task.estimated_complexity else None
            ),
            "nature": str(task.nature) if task.nature else None,
            "plan": task.plan,
            "progress_updates": list(task.progress_updates or [])[
                -_MAX_PROGRESS_UPDATES:
            ],
            "dev_notes": task.dev_notes,
            "qa_notes": task.qa_notes,
            "auditor_notes": task.auditor_notes,
            "pr_reviewer_notes": task.pr_reviewer_notes,
            "doc_notes": task.doc_notes,
            "quick_context": task.quick_context,
            "branch_name": task.branch_name,
            "pr_number": task.pr_number,
            "pr_url": task.pr_url,
        }

    # ------------------------------------------------------------------ #
    # directives
    # ------------------------------------------------------------------ #

    async def get_directive(self, directive_id: UUID) -> SecretaryDirectiveTable | None:
        result = await self.session.execute(
            select(SecretaryDirectiveTable).where(
                SecretaryDirectiveTable.id == directive_id
            )
        )
        return result.scalar_one_or_none()

    async def list_directives(
        self, status: DirectiveStatus | None = None
    ) -> list[SecretaryDirectiveTable]:
        stmt = select(SecretaryDirectiveTable).order_by(
            SecretaryDirectiveTable.requested_at.desc()
        )
        if status is not None:
            stmt = stmt.where(SecretaryDirectiveTable.status == status.value)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def submit_directive(
        self, kind: DirectiveKind, payload: dict[str, Any], requested_by: UUID
    ) -> SecretaryDirectiveTable:
        """Queue a gated directive, or execute a direct one immediately."""
        self._validate_payload(kind, payload)
        row = SecretaryDirectiveTable(
            kind=kind.value,
            payload=payload,
            status=DirectiveStatus.PENDING.value,
            requested_by=requested_by,
        )
        self.session.add(row)
        await self.session.flush()
        if kind in GATED_KINDS:
            await self._notify_ceo_pending(row)
            return row
        await self._run(row)
        return row

    async def confirm_directive(
        self, directive_id: UUID, decided_by: UUID
    ) -> SecretaryDirectiveTable:
        row = await self._pending_or_raise(directive_id)
        row.decided_by = require_uuid(decided_by)
        await self._run(row)
        return row

    async def reject_directive(
        self, directive_id: UUID, decided_by: UUID, reason: str | None = None
    ) -> SecretaryDirectiveTable:
        row = await self._pending_or_raise(directive_id)
        row.status = DirectiveStatus.REJECTED.value
        row.decided_by = require_uuid(decided_by)
        row.decided_at = datetime.now(UTC)
        row.result = reason or "declined by CEO"
        await self.session.flush()
        return row

    @staticmethod
    def to_dict(row: SecretaryDirectiveTable) -> dict[str, Any]:
        return {
            "id": str(row.id),
            "kind": row.kind,
            "status": row.status,
            "payload": dict(row.payload or {}),
            "requested_by": str(row.requested_by),
            "requested_at": row.requested_at.isoformat() if row.requested_at else None,
            "decided_by": str(row.decided_by) if row.decided_by else None,
            "decided_at": row.decided_at.isoformat() if row.decided_at else None,
            "result": row.result,
        }

    # ------------------------------------------------------------------ #
    # internals
    # ------------------------------------------------------------------ #

    async def _pending_or_raise(self, directive_id: UUID) -> SecretaryDirectiveTable:
        row = await self.get_directive(directive_id)
        if row is None:
            raise NotFoundError("directive", str(directive_id))
        if row.status != DirectiveStatus.PENDING.value:
            raise ConflictError(
                f"directive is '{row.status}', not pending",
                resource_type="secretary_directive",
            )
        return row

    @staticmethod
    def _validate_payload(kind: DirectiveKind, payload: dict[str, Any]) -> None:
        missing = [k for k in _REQUIRED_PAYLOAD[kind] if k not in payload]
        if missing:
            raise ValidationError(f"{kind.value} requires payload keys: {missing}")

    async def _run(self, row: SecretaryDirectiveTable) -> None:
        try:
            row.result = await self._execute(DirectiveKind(row.kind), row.payload or {})
            row.status = DirectiveStatus.EXECUTED.value
        except (
            ConflictError,
            NotFoundError,
            ValidationError,
            ValueError,
            KeyError,
        ) as exc:
            row.status = DirectiveStatus.FAILED.value
            row.result = f"error: {exc}"
        row.decided_at = datetime.now(UTC)
        await self.session.flush()

    async def _execute(self, kind: DirectiveKind, payload: dict[str, Any]) -> str:
        if kind in (DirectiveKind.RELAY_MESSAGE, DirectiveKind.ANNOUNCE):
            channel = (
                _ANNOUNCE_CHANNEL
                if kind is DirectiveKind.ANNOUNCE
                else str(payload["channel"])
            )
            await get_messaging_service(self.session).post_to_channel(
                agent_id=_CEO_ID, channel_slug=channel, content=str(payload["text"])
            )
            return f"posted to #{channel}"
        if kind is DirectiveKind.UPDATE_CHARTER:
            await get_company_goals_service(self.session).upsert(
                dict(payload["charter"]), updated_by=_CEO_ID
            )
            return "charter updated"
        if kind is DirectiveKind.APPROVE_PITCH:
            await get_pitch_service(self.session).approve(
                require_uuid(payload["pitch_id"]),
                str(payload.get("notes", "approved by CEO via Secretary")),
                _CEO_ID,
            )
            return "pitch approved and provisioned"
        return await self._control_task(payload)

    # Content fields the Secretary may edit on CEO confirmation — the FULL
    # surface (Secretary FULL task access). Status is never set here — it has
    # its own audited path (the "start"/"cancel"/"override" actions below);
    # git fields (branch/PR) are never editable — they follow the git
    # workflow. ``assigned_to`` rides the edit too but is handled separately
    # (see ``_edit_task``) since it needs claim-aware reassignment, not a
    # plain field set.
    _EDITABLE_TASK_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {
            "title",
            "description",
            "acceptance_criteria",
            "priority",
            "team",
            "estimated_complexity",
            "nature",
            "assigned_to",
        }
    )

    # Enum-typed content fields need coercion from the raw JSON string before
    # they reach TaskService.update() (a generic setattr passthrough with no
    # type coercion of its own).
    _ENUM_TASK_FIELDS: ClassVar[dict[str, Any]] = {
        "team": Team,
        "estimated_complexity": Complexity,
        "nature": TaskNature,
    }

    _ASSIGNMENT_FIELD = "assigned_to"

    async def _control_task(self, payload: dict[str, Any]) -> str:
        task_svc = get_task_service(self.session)
        task_id = require_uuid(payload["task_id"])
        action = str(payload["action"])
        notes = str(payload.get("notes", "via Secretary on CEO command"))
        if action == "edit":
            return await self._edit_task(task_svc, task_id, payload)
        if action == "start":
            await task_svc.approve_and_start(task_id, notes)
            return "task started"
        if action == "cancel":
            await task_svc.admin_set_status(
                task_id, TaskStatus.CANCELLED, actor_id=_CEO_ID, actor_role="ceo"
            )
            return "task cancelled"
        if action == "override":
            new_status = TaskStatus(str(payload["status"]))
            await task_svc.admin_set_status(
                task_id, new_status, actor_id=_CEO_ID, actor_role="ceo"
            )
            return f"task set to {new_status.value}"
        raise ValidationError(f"unknown task action: {action!r}")

    async def _edit_task(
        self, task_svc: TaskService, task_id: UUID, payload: dict[str, Any]
    ) -> str:
        """Apply the Secretary's edit — content fields + optional reassign.

        ``assigned_to`` is popped out and routed through claim-aware
        reassignment (``_reassign_task``) rather than a plain field set;
        every other allowlisted field goes through ``TaskService.update``
        after enum coercion.
        """
        fields = dict(payload.get("fields") or {})
        illegal = set(fields) - self._EDITABLE_TASK_FIELDS
        if not fields or illegal:
            raise ValidationError(
                "edit accepts only "
                f"{sorted(self._EDITABLE_TASK_FIELDS)}; got "
                f"{sorted(fields) or 'nothing'}"
            )
        reassign_to = fields.pop(self._ASSIGNMENT_FIELD, _UNSET)
        for field, enum_cls in self._ENUM_TASK_FIELDS.items():
            if field in fields:
                fields[field] = enum_cls(str(fields[field]))

        results: list[str] = []
        if fields:
            await task_svc.update(task_id, **fields)
            results.append(f"fields updated: {', '.join(sorted(fields))}")
        if reassign_to is not _UNSET:
            new_assignee = await self._resolve_assignee(reassign_to)
            await self._reassign_task(task_svc, task_id, new_assignee)
            results.append(
                f"reassigned to {reassign_to}"
                if new_assignee is not None
                else "unassigned"
            )
        return "; ".join(results)

    async def _reassign_task(
        self, task_svc: TaskService, task_id: UUID, new_assignee: UUID | None
    ) -> None:
        """Route reassignment through the task service's claim-aware paths.

        An active claim (``claimed``/``in_progress``) reseeds the heartbeat
        via ``reassign_active_claim`` so the new assignee isn't immediately
        stale to the reaper; everything else (review-state handoffs, or an
        explicit unassign) goes through the general ``reassign`` — never a
        naive ``setattr`` on ``assigned_to``.
        """
        if new_assignee is not None:
            task = await task_svc.get(task_id)
            if task is not None and task.status in (
                TaskStatus.CLAIMED,
                TaskStatus.IN_PROGRESS,
            ):
                reassigned = await task_svc.reassign_active_claim(task_id, new_assignee)
                if reassigned is not None:
                    return
        await task_svc.reassign(task_id, new_assignee)

    async def _resolve_assignee(self, raw: Any) -> UUID | None:
        """Resolve an edit's ``assigned_to`` value to an agent UUID.

        Accepts ``None`` (explicit unassign), a UUID string, or an agent slug
        (e.g. ``"be-dev-1"``) — the same slug convention the REST PATCH path
        resolves for the CEO's chat, which refers to agents by name.
        """
        if raw is None:
            return None
        candidate = str(raw)
        try:
            return require_uuid(candidate)
        except InvalidIdentifierError:
            pass
        agent_row = await get_agent_by_slug(self.session, candidate)
        if agent_row is None:
            raise ValidationError(f"no agent with slug or UUID {candidate!r}")
        return require_uuid(agent_row.id)

    async def _notify_ceo_pending(self, row: SecretaryDirectiveTable) -> None:
        from roboco.services.notification import NotificationService

        await NotificationService().send_ack_notification(
            from_agent="secretary-1",
            to_agent="ceo",
            body=(
                f"[secretary] A {row.kind} directive needs your confirmation "
                "before it runs. Review it in the Secretary surface."
            ),
        )


def get_secretary_service(session: AsyncSession) -> SecretaryService:
    """Construct a SecretaryService bound to ``session``."""
    return SecretaryService(session)
