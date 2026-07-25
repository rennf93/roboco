"""SpackleEngine — Spackle (Board Program), project-scoped.

Mirrors ``roboco.services.pest_control_engine.PestControlEngine``'s "detect ->
originate a CEO-gated artifact -> hold" shape exactly — same rotation, same
dedup, same held-exploration-task construction. The one deliberate
difference (spec §4 / docs/internal/specs/2026-07-24-board-programs-design.md
§4 "Spackle"): this program carries NO heavy server-side inventory engine.
Pest Control assembles rework/findings-ledger evidence server-side because
the Product Owner has no SQL access to run those aggregate queries itself;
Spackle's inventory diffing (API routes vs panel surfaces, armed flags vs
docs, docs claims vs code, coverage holes, dead-end panel tabs) is the PO's
own read-tool work, ordered explicitly by the orchestrator's spawn prompt
(``_build_spackle_prompt``) — so this engine has no ``evidence_context``
method at all, only prior-cycle LEARN (injected by the orchestrator, same as
every other program) plus the target project.

* **No master enable flag.** Armed via ``roboco.services.board_programs.
  program_armed`` — the settings-store ``board_program.spackle.enabled`` key
  is the ONLY arming path (``_legacy_enabled`` returns False for a key it
  doesn't recognize); off by default like every other program.
* **One open cycle at a time.** Dedup by ``source=board_spackle``
  non-terminal tasks.
* **The engine never authors content.** It opens ONE held, PENDING
  exploration task assigned to the Product Owner (``Team.BOARD``,
  ``confirmed_by_human=False``) targeting the opted-in project this program
  has gone longest without exploring; the board dispatcher spawns the PO,
  who audits the project's surface area and calls ``propose_gap_fill``
  exactly once. Approved items materialize into BACKLOG only via the CEO's
  per-item approve (``SpackleService``) — this engine never starts anything.
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
from roboco.services.task import SPACKLE_SOURCE, TaskCreateRequest, get_task_service

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import ProjectTable, TaskTable
    from roboco.services.task import TaskService

_EXPLORATION_TITLE = "Spackle exploration cycle"
_EXPLORATION_DESCRIPTION = (
    "Audit half-shipped surface area: API routes with no panel surface (and "
    "vice versa), armed flags with no docs, docs-site promises the code "
    "doesn't keep, coverage holes by module, dead-end panel tabs. Propose up "
    f"to {PROGRAMS['spackle'].max_items_per_cycle} evidence-backed gap-fill "
    "tasks via propose_gap_fill() — each item is reviewed and "
    "approved/rejected individually by the CEO; nothing here auto-starts."
)


class SpackleEngine(BaseService):
    """Originate ONE held spackle-exploration cycle for the Product Owner."""

    service_name = "spackle_engine"

    async def run_cycle(self) -> TaskTable | None:
        """Originate one held exploration task, or None (no-op).

        No-ops when the program isn't armed, a cycle is already open, or no
        project has opted in (``projects.board_programs`` contains
        ``"spackle"``). Never authors content itself — the Product Owner
        does, via ``propose_gap_fill`` once spawned by the board dispatcher.
        """
        if not await program_armed(self.session, "spackle"):
            return None
        task_svc = get_task_service(self.session)
        if await task_svc.list_open_spackle_cycles():
            return None  # one open cycle at a time
        projects = await get_board_program_engine(self.session).opted_in_projects(
            PROGRAMS["spackle"]
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
        project as if covered would be a lie. Mirrors
        ``PestControlEngine._originate``."""
        target = await pick_rotation_target(
            self.session, projects, source=SPACKLE_SOURCE
        )
        queued = [p.slug for p in projects if p.id != target.id]
        description = f"{_EXPLORATION_DESCRIPTION} This cycle's target: {target.slug}."
        if queued:
            description += f" Queued for subsequent cycles: {', '.join(queued)}."
        task = await task_svc.create(
            TaskCreateRequest(
                title=_EXPLORATION_TITLE,
                description=description,
                acceptance_criteria=[
                    "propose_gap_fill() is called once with 1-"
                    f"{PROGRAMS['spackle'].max_items_per_cycle} "
                    "evidence-backed gap-fill item drafts",
                ],
                team=Team.BOARD,
                assigned_to=_foundation.AGENTS["product-owner"].uuid,
                created_by=_foundation.AGENTS["system"].uuid,
                task_type=TaskType.ADMINISTRATIVE,
                nature=TaskNature.NON_TECHNICAL,
                estimated_complexity=Complexity.LOW,
                project_id=cast("UUID", target.id),
                status=TaskStatus.PENDING,
                source=SPACKLE_SOURCE,
                confirmed_by_human=False,  # HELD; board-dispatched, not delivery
            )
        )
        await self.session.flush()
        self.log.info(
            "spackle exploration cycle opened (Product Owner)",
            task_id=str(task.id),
            project_slug=target.slug,
        )
        return task


def get_spackle_engine(session: AsyncSession) -> SpackleEngine:
    """Build a SpackleEngine for ``session``."""
    return SpackleEngine(session)
