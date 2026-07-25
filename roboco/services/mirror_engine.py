"""MirrorEngine — Mirror (Board Program), project-scoped.

Mirrors ``roboco.services.spackle_engine.SpackleEngine``'s "detect ->
originate a CEO-gated artifact -> hold" shape exactly — same rotation, same
dedup, same held-exploration-task construction. Same deliberate difference
as Spackle (spec §4 / docs/internal/specs/2026-07-24-board-programs-design.md
§4 "Mirror"): this program carries NO heavy server-side inventory engine —
the messaging audit (README claims vs shipped features, docs-site promises
vs code, charter alignment) is the Head of Marketing's own read-tool work,
ordered explicitly by the orchestrator's spawn prompt
(``_build_mirror_prompt``) — so this engine has no ``evidence_context``
method at all, only prior-cycle LEARN (injected by the orchestrator, same as
every other program) plus the target project.

* **No master enable flag.** Armed via ``roboco.services.board_programs.
  program_armed`` — the settings-store ``board_program.mirror.enabled`` key
  is the ONLY arming path (``_legacy_enabled`` returns False for a key it
  doesn't recognize); off by default like every other program.
* **One open cycle at a time.** Dedup by ``source=board_mirror``
  non-terminal tasks.
* **The engine never authors content.** It opens ONE held, PENDING
  exploration task assigned to the Head of Marketing (``Team.BOARD``,
  ``confirmed_by_human=False``) targeting the opted-in project this program
  has gone longest without exploring; the board dispatcher spawns the HoM,
  who audits the project's messaging surfaces and calls
  ``propose_messaging_fixes`` exactly once. Approved items materialize into
  BACKLOG only via the CEO's per-item approve (``MirrorService``) — this
  engine never starts anything.
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
from roboco.services.task import MIRROR_SOURCE, TaskCreateRequest, get_task_service

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import ProjectTable, TaskTable
    from roboco.services.task import TaskService

_EXPLORATION_TITLE = "Mirror exploration cycle"
_EXPLORATION_DESCRIPTION = (
    "Audit messaging surfaces (README, docs-site, website) against the "
    "charter and shipped reality: claims the copy makes that the code "
    "doesn't back, and shipped capabilities the copy doesn't mention. "
    "Propose up to "
    f"{PROGRAMS['mirror'].max_items_per_cycle} evidence-backed docs tasks via "
    "propose_messaging_fixes() — each item is reviewed and "
    "approved/rejected individually by the CEO; nothing here auto-starts."
)


class MirrorEngine(BaseService):
    """Originate ONE held mirror-exploration cycle for the Head of Marketing."""

    service_name = "mirror_engine"

    async def run_cycle(self) -> TaskTable | None:
        """Originate one held exploration task, or None (no-op).

        No-ops when the program isn't armed, a cycle is already open, or no
        project has opted in (``projects.board_programs`` contains
        ``"mirror"``). Never authors content itself — the Head of Marketing
        does, via ``propose_messaging_fixes`` once spawned by the board
        dispatcher.
        """
        if not await program_armed(self.session, "mirror"):
            return None
        task_svc = get_task_service(self.session)
        if await task_svc.list_open_mirror_cycles():
            return None  # one open cycle at a time
        projects = await get_board_program_engine(self.session).opted_in_projects(
            PROGRAMS["mirror"]
        )
        if not projects:
            return None
        return await self._originate(task_svc, projects)

    async def _originate(
        self, task_svc: TaskService, projects: list[ProjectTable]
    ) -> TaskTable:
        """Open ONE PENDING, HELD exploration task assigned to the Head of
        Marketing, targeting the opted-in project this program has gone
        LONGEST without exploring (round-robin: a never-explored project
        always wins over an explored one; ties break by ``projects``' own
        deterministic order). The HoM only has the target project's repo
        mounted, so the description names it as this cycle's actual subject
        and lists the rest as queued for later cycles — naming every opted
        project as if covered would be a lie. Mirrors
        ``SpackleEngine._originate``."""
        target = await pick_rotation_target(
            self.session, projects, source=MIRROR_SOURCE
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
                    "propose_messaging_fixes() is called once with 1-"
                    f"{PROGRAMS['mirror'].max_items_per_cycle} "
                    "evidence-backed messaging-fix item drafts",
                ],
                team=Team.BOARD,
                assigned_to=_foundation.AGENTS["head-marketing"].uuid,
                created_by=_foundation.AGENTS["system"].uuid,
                task_type=TaskType.ADMINISTRATIVE,
                nature=TaskNature.NON_TECHNICAL,
                estimated_complexity=Complexity.LOW,
                project_id=cast("UUID", target.id),
                status=TaskStatus.PENDING,
                source=MIRROR_SOURCE,
                confirmed_by_human=False,  # HELD; board-dispatched, not delivery
            )
        )
        await self.session.flush()
        self.log.info(
            "mirror exploration cycle opened (Head of Marketing)",
            task_id=str(task.id),
            project_slug=target.slug,
        )
        return task


def get_mirror_engine(session: AsyncSession) -> MirrorEngine:
    """Build a MirrorEngine for ``session``."""
    return MirrorEngine(session)
