"""CoronerEngine — Coroner (Board Program), the Auditor's event-triggered
postmortem (docs/internal/specs/2026-07-24-board-programs-design.md §4).

Unlike every other registered program, Coroner's trigger is ``TriggerKind.
EVENT`` — it is never opened by ``BoardProgramEngine.run_due_programs`` (which
skips any non-CRON program) or a "run now" call. A cycle opens ONLY through
``open_for_incident``, called directly from three event hooks: a task's 3rd
bounce into ``needs_revision`` (``TaskService._emit_status_transition_audit``),
a cancel after work started (``TaskService.cancel``), and a budget-block
(the orchestrator's task-budget sweep). Because there is no loop tick to route
it through, ``open_for_incident`` bypasses ``roboco.services.board_programs.
_ORIGINATORS`` entirely and builds its own ``BoardProgramCycleTable`` LEARN
row inline — see ``_originate_and_record``'s docstring there for why the
dict-of-callables shape doesn't fit an event-driven program with extra
per-call context (``incident_task_id``, ``kind``) that ``_ORIGINATORS``'
uniform ``(session) -> TaskTable | None`` signature has no room for.

* **No master enable flag.** Armed via ``roboco.services.board_programs.
  program_armed`` — the settings-store ``board_program.coroner.enabled`` key
  is the ONLY arming path; off by default like every other program.
* **One open autopsy at a time.** Dedup by ``source=board_coroner``
  non-terminal tasks — a second incident while one is open is skipped
  (logged), never queued; the incident itself is not held responsible for a
  postmortem it didn't get.
* **The engine never authors content.** It opens ONE held, PENDING
  postmortem-exploration task assigned to the Auditor (``Team.BOARD``,
  ``confirmed_by_human=False``); the board dispatcher spawns the Auditor, who
  autopsies the incident and calls ``propose_postmortem`` exactly once, which
  completes the task in the same call (mirrors the X feature-spotlight shape,
  not roadmap/pest-control's stays-open-for-per-item-decisions shape — a
  postmortem is one process change, not a list of items).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType, Team
from roboco.services.base import BaseService
from roboco.services.board_programs import program_armed
from roboco.services.repositories.review_findings import ReviewFindingsRepository
from roboco.services.task import CORONER_SOURCE, TaskCreateRequest, get_task_service

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import TaskTable
    from roboco.services.task import TaskService

_EXPLORATION_TITLE = "Coroner postmortem"

# Context caps — mirrors PestControlEngine.evidence_context's bounded prompt
# posture: the findings ledger / transition history for a heavily-bounced
# task must stay bounded regardless of how long its history has grown.
_FINDINGS_LIMIT = 10
_TRANSITION_HISTORY_LIMIT = 20

_INCIDENT_KIND_LABELS: dict[str, str] = {
    "bounced": "bounced into needs_revision 3+ times",
    "cancelled": "cancelled after work had started",
    "budget": "blocked on a budget breach",
}


class CoronerEngine(BaseService):
    """Originate ONE held postmortem-exploration cycle for the Auditor."""

    service_name = "coroner_engine"

    async def open_for_incident(
        self, incident_task_id: UUID, *, kind: str
    ) -> TaskTable | None:
        """Autopsy ``incident_task_id`` (``kind`` in bounced|cancelled|budget),
        or no-op — the EVENT-triggered entry point every hook calls directly.

        No-ops when the program isn't armed, a ``board_programs`` maintenance
        pause is active, an autopsy is already open, or the incident task is
        unresolvable. Never authors content itself (the Auditor does, via
        ``propose_postmortem`` once spawned).
        """
        from roboco.services.maintenance_pause import PauseScope, is_paused

        if not await program_armed(self.session, "coroner") or await is_paused(
            self.session, PauseScope.BOARD_PROGRAMS
        ):
            return None
        task_svc = get_task_service(self.session)
        if await task_svc.list_open_coroner_cycles():
            self.log.info(
                "coroner: an autopsy is already open, skipping this incident",
                incident_task_id=str(incident_task_id),
                kind=kind,
            )
            return None
        incident = await task_svc.get(incident_task_id)
        if incident is None:
            return None
        task = await self._originate(task_svc, incident, kind)
        await self._record_cycle(task)
        return task

    async def _originate(
        self, task_svc: TaskService, incident: TaskTable, kind: str
    ) -> TaskTable:
        """Open ONE PENDING, HELD postmortem-exploration task assigned to the
        Auditor, naming the incident + kind. Project is the incident's own
        (an autopsy is about that incident, wherever it lived); a branchless
        incident (no ``project_id``) falls back to the RoboCo project, same
        posture as ``RoadmapEngine._roboco_project`` — every task needs one."""
        label = _INCIDENT_KIND_LABELS.get(kind, kind)
        description = (
            f"Task {incident.title!r} ({str(incident.id)[:8]}) was {label}. "
            "Read its full journey — evidence(), the findings ledger, journal "
            "trail — determine what failed, which stage, and the systemic "
            "cause, then propose ONE process change via propose_postmortem(): "
            "a playbook draft, a prompt fix, or a conventions rule."
        )
        project_id = incident.project_id or await self._roboco_project_id()
        task = await task_svc.create(
            TaskCreateRequest(
                title=_EXPLORATION_TITLE,
                description=description,
                acceptance_criteria=[
                    "propose_postmortem() is called exactly once naming the "
                    "root cause and ONE process change",
                ],
                team=Team.BOARD,
                assigned_to=_foundation.AGENTS["auditor"].uuid,
                created_by=_foundation.AGENTS["system"].uuid,
                task_type=TaskType.ADMINISTRATIVE,
                nature=TaskNature.NON_TECHNICAL,
                estimated_complexity=Complexity.LOW,
                project_id=cast("UUID", project_id),
                status=TaskStatus.PENDING,
                source=CORONER_SOURCE,
                confirmed_by_human=False,  # HELD; board-dispatched, not delivery
            )
        )
        markers.set_coroner_incident(
            task,
            {
                "incident_task_id": str(incident.id),
                "kind": kind,
                "revision_count": incident.revision_count or 0,
                "title": incident.title,
            },
        )
        await self.session.flush()
        self.log.info(
            "coroner postmortem cycle opened (Auditor)",
            task_id=str(task.id),
            incident_task_id=str(incident.id),
            kind=kind,
        )
        return task

    async def complete_with_postmortem(
        self, task: TaskTable, payload: dict[str, Any]
    ) -> None:
        """Persist the Auditor's postmortem payload and complete the autopsy
        task in one step — mirrors ``XEngine.materialize_feature_spotlight``'s
        atomic-complete shape (a postmortem is one report, not a per-item
        queue, so there is no separate CEO decision step to stay open for).
        Called only from the ``propose_postmortem`` content verb, after that
        verb has already validated the payload and (for a playbook-kind
        change) successfully drafted the playbook."""
        markers.set_coroner_postmortem(task, payload)
        task.status = TaskStatus.COMPLETED
        await self.session.flush()
        self.log.info("coroner postmortem recorded (Auditor)", task_id=str(task.id))

    async def _roboco_project_id(self) -> UUID | None:
        from roboco.config import settings
        from roboco.services.project import get_project_service

        slug = (settings.self_heal_project_slug or "roboco-api").strip()
        project = await get_project_service(self.session).get_by_slug(slug)
        return cast("UUID | None", project.id) if project is not None else None

    async def _record_cycle(self, task: TaskTable) -> None:
        """The LEARN ledger row — inline, not through ``_originate_and_record``
        (see this module's docstring for why the event path bypasses
        ``_ORIGINATORS``)."""
        from roboco.db.tables import BoardProgramCycleTable

        self.session.add(
            BoardProgramCycleTable(
                program_key="coroner",
                exploration_task_id=task.id,
                opened_at=datetime.now(UTC),
            )
        )
        await self.session.flush()

    async def incident_context(self, incident_task_id: UUID) -> str:
        """Server-assembled evidence for the Auditor's prompt — the incident's
        findings-ledger rows + status-transition history, capped so the
        prompt stays bounded. Mirrors ``PestControlEngine.evidence_context``'s
        shape (the agent cannot run these queries itself)."""
        sections = [
            ("Findings ledger", await self._findings_summary(incident_task_id)),
            ("Transition history", await self._transition_history(incident_task_id)),
        ]
        return "\n\n".join(
            f"{title}:\n" + "\n".join(lines) for title, lines in sections if lines
        )

    async def _findings_summary(self, incident_task_id: UUID) -> list[str]:
        rows = await ReviewFindingsRepository(self.session).list_for_task(
            incident_task_id, limit=_FINDINGS_LIMIT
        )
        return [
            f"- [{r.severity}] round {r.round} ({r.origin}, {r.status}): "
            f"{r.file or 'n/a'}:{r.line or '?'} — {r.criterion}"
            for r in rows
        ]

    async def _transition_history(self, incident_task_id: UUID) -> list[str]:
        from sqlalchemy import select

        from roboco.db.tables import AuditLogTable

        result = await self.session.execute(
            select(AuditLogTable.event_type, AuditLogTable.timestamp)
            .where(
                AuditLogTable.target_type == "task",
                AuditLogTable.target_id == incident_task_id,
            )
            .order_by(AuditLogTable.timestamp)
            .limit(_TRANSITION_HISTORY_LIMIT)
        )
        return [f"- {ts.isoformat()} {event}" for event, ts in result.all()]


def get_coroner_engine(session: AsyncSession) -> CoronerEngine:
    """Construct a CoronerEngine bound to ``session``."""
    return CoronerEngine(session)
