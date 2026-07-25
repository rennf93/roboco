"""PestControlEngine — Pest Control (Board Program), project-scoped.

Mirrors ``roboco.services.roadmap_engine.RoadmapEngine``'s "detect -> originate
a CEO-gated artifact -> hold" shape, with two differences that follow from the
spec (docs/internal/specs/2026-07-24-board-programs-design.md §4): the cycle
targets an OPTED-IN project (``scope="project"`` — see
``roboco.foundation.policy.board_programs.project_participates``) instead of
RoboCo unconditionally, and the exploration prompt needs evidence the Product
Owner can't gather itself (findings-ledger aggregates, rework hotspots), so
this engine also assembles that evidence for the orchestrator's prompt builder
(``evidence_context``) — a pure DB read, no content authored here either way.

* **No master enable flag.** Armed via ``roboco.services.board_programs.
  program_armed`` — the settings-store ``board_program.pest_control.enabled``
  key is the ONLY arming path (``_legacy_enabled`` returns False for a key it
  doesn't recognize); off by default like every other program.
* **One open cycle at a time.** Dedup by ``source=board_pest_control``
  non-terminal tasks.
* **The engine never authors content.** It opens ONE held, PENDING
  exploration task assigned to the Product Owner (``Team.BOARD``,
  ``confirmed_by_human=False``) targeting the FIRST opted-in project; the
  board dispatcher spawns the PO, who explores and calls ``propose_bug_hunt``
  exactly once. Approved items materialize into BACKLOG only via the CEO's
  per-item approve (``PestControlService``) — this engine never starts
  anything.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from roboco.foundation import identity as _foundation
from roboco.foundation.policy.board_programs import PROGRAMS
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType, Team
from roboco.services.base import BaseService
from roboco.services.board_programs import (
    get_board_program_engine,
    pick_rotation_target,
    program_armed,
)
from roboco.services.repositories.review_findings import ReviewFindingsRepository
from roboco.services.task import (
    PEST_CONTROL_SOURCE,
    TaskCreateRequest,
    get_task_service,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import ProjectTable, TaskTable
    from roboco.services.task import TaskService

_EXPLORATION_TITLE = "Pest Control exploration cycle"
_EXPLORATION_DESCRIPTION = (
    "Hunt latent defects the org records but nobody reads: the findings "
    "ledger (recurring/waived-minor clusters), revision_count hotspots, and "
    "TODO/ponytail debt in the repo. Propose up to "
    f"{PROGRAMS['pest_control'].max_items_per_cycle} evidence-backed bug "
    "tasks via propose_bug_hunt() — each item is reviewed and "
    "approved/rejected individually by the CEO; nothing here auto-starts."
)

# Evidence-context caps — the prompt injection stays bounded regardless of how
# large the ledger/backlog has grown.
_HOTSPOT_LIMIT = 10
_FINDINGS_LIMIT = 10
_MIN_REVISION_COUNT = 2
_MIN_RECURRING_FINDINGS = 2


class PestControlEngine(BaseService):
    """Originate ONE held pest-control-exploration cycle for the Product Owner."""

    service_name = "pest_control_engine"

    async def run_cycle(self) -> TaskTable | None:
        """Originate one held exploration task, or None (no-op).

        No-ops when the program isn't armed, a cycle is already open, or no
        project has opted in (``projects.board_programs`` contains
        ``"pest_control"``). Never authors content itself — the Product Owner
        does, via ``propose_bug_hunt`` once spawned by the board dispatcher.
        """
        if not await program_armed(self.session, "pest_control"):
            return None
        task_svc = get_task_service(self.session)
        if await task_svc.list_open_pest_control_cycles():
            return None  # one open cycle at a time
        projects = await get_board_program_engine(self.session).opted_in_projects(
            PROGRAMS["pest_control"]
        )
        if not projects:
            return None
        return await self._originate(task_svc, projects)

    async def _originate(
        self, task_svc: TaskService, projects: list[ProjectTable]
    ) -> TaskTable:
        """Open ONE PENDING, HELD exploration task assigned to the Product
        Owner, targeting the opted-in project this program has gone LONGEST
        without exploring (round-robin: a never-explored project always
        wins over an explored one; ties break by ``projects``' own
        deterministic order). The PO only has the target project's repo
        mounted, so the description names it as this cycle's actual subject
        and lists the rest as queued for later cycles — naming every opted
        project as if covered would be a lie."""
        target = await self._pick_rotation_target(projects)
        queued = [p.slug for p in projects if p.id != target.id]
        description = f"{_EXPLORATION_DESCRIPTION} This cycle's target: {target.slug}."
        if queued:
            description += f" Queued for subsequent cycles: {', '.join(queued)}."
        task = await task_svc.create(
            TaskCreateRequest(
                title=_EXPLORATION_TITLE,
                description=description,
                acceptance_criteria=[
                    "propose_bug_hunt() is called once with 1-"
                    f"{PROGRAMS['pest_control'].max_items_per_cycle} "
                    "evidence-backed bug item drafts",
                ],
                team=Team.BOARD,
                assigned_to=_foundation.AGENTS["product-owner"].uuid,
                created_by=_foundation.AGENTS["system"].uuid,
                task_type=TaskType.ADMINISTRATIVE,
                nature=TaskNature.NON_TECHNICAL,
                estimated_complexity=Complexity.LOW,
                project_id=cast("UUID", target.id),
                status=TaskStatus.PENDING,
                source=PEST_CONTROL_SOURCE,
                confirmed_by_human=False,  # HELD; board-dispatched, not delivery
            )
        )
        await self.session.flush()
        self.log.info(
            "pest-control exploration cycle opened (Product Owner)",
            task_id=str(task.id),
            project_slug=target.slug,
        )
        return task

    async def _pick_rotation_target(self, projects: list[ProjectTable]) -> ProjectTable:
        """The opted-in project due this cycle — see
        ``roboco.services.board_programs.pick_rotation_target`` (shared by
        every project-scoped program's rotation, e.g. Spackle)."""
        return await pick_rotation_target(
            self.session, projects, source=PEST_CONTROL_SOURCE
        )

    async def evidence_context(self) -> str:
        """Server-assembled evidence for the PO's prompt — rework hotspots +
        findings-ledger aggregates, capped so the prompt stays bounded. The
        PO cannot run these aggregate queries itself (no SQL tool), so the
        engine gathers them ahead of the spawn (mirrors ``prior_cycle_context``'s
        best-effort, orchestrator-injected shape)."""
        sections = [
            ("Rework hotspots (revision_count >= 2)", await self._rework_hotspots()),
            ("Recurring findings by file", await self._recurring_findings()),
            ("Waived-minor finding clusters by file", await self._waived_minor()),
        ]
        return "\n\n".join(
            f"{title}:\n" + "\n".join(lines) for title, lines in sections if lines
        )

    async def _rework_hotspots(self) -> list[str]:
        from sqlalchemy import select

        from roboco.db.tables import ProjectTable, TaskTable

        result = await self.session.execute(
            select(TaskTable.title, TaskTable.revision_count, ProjectTable.slug)
            .join(ProjectTable, TaskTable.project_id == ProjectTable.id, isouter=True)
            .where(TaskTable.revision_count >= _MIN_REVISION_COUNT)
            .order_by(TaskTable.revision_count.desc())
            .limit(_HOTSPOT_LIMIT)
        )
        return [
            f"- {title} ({slug or 'no project'}) — bounced {count}x"
            for title, count, slug in result.all()
        ]

    async def _recurring_findings(self) -> list[str]:
        rows = await ReviewFindingsRepository(self.session).recurring_file_counts(
            min_count=_MIN_RECURRING_FINDINGS, limit=_FINDINGS_LIMIT
        )
        return [f"- {file}: {count} findings" for file, count in rows]

    async def _waived_minor(self) -> list[str]:
        rows = await ReviewFindingsRepository(self.session).waived_minor_file_counts(
            min_count=_MIN_RECURRING_FINDINGS, limit=_FINDINGS_LIMIT
        )
        return [f"- {file}: {count} waived-minor findings" for file, count in rows]


def get_pest_control_engine(session: AsyncSession) -> PestControlEngine:
    """Build a PestControlEngine for ``session``."""
    return PestControlEngine(session)
