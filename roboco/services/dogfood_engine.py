"""DogfoodEngine — Dogfood (Board Program), project-scoped.

Mirrors ``roboco.services.spackle_engine.SpackleEngine``'s "detect ->
originate a CEO-gated artifact -> hold" shape exactly — same rotation, same
dedup, same held-exploration-task construction, no heavy server-side
evidence engine (walking the product live is the PO's own tool work, ordered
explicitly by the orchestrator's spawn prompt, ``_build_dogfood_prompt``).

The one real difference (spec §4 / docs/internal/specs/2026-07-24-board-
programs-design.md §4 "Dogfood"): the program's trigger is ``TriggerKind.
EVENT``, not ``CRON`` — a cycle opens off a release-publish hook
(``ReleaseProposalService._draft_dogfood_walk``) or a CEO "run now", both of
which route through ``BoardProgramEngine.open_program_cycle`` -> this
engine's ``run_cycle``. Unlike Coroner's EVENT program, Dogfood needs no
external incident id to target — the next opted-in project in rotation is
enough — so this engine registers a REAL ``run_cycle`` in
``roboco.services.board_programs._ORIGINATORS`` (Coroner's entry there is a
never-firing stub) and "run now" works exactly like it would for a CRON
program; the cron loop itself still never calls it (``program_due`` refuses
any non-CRON trigger).

* **No master enable flag.** Armed via ``roboco.services.board_programs.
  program_armed`` — the settings-store ``board_program.dogfood.enabled`` key
  is the ONLY arming path (``_legacy_enabled`` returns False for a key it
  doesn't recognize); off by default like every other program.
* **One open cycle at a time.** Dedup by ``source=board_dogfood``
  non-terminal tasks.
* **The engine never authors content.** It opens ONE held, PENDING
  exploration task assigned to the Product Owner (``Team.BOARD``,
  ``confirmed_by_human=False``) targeting the opted-in project this program
  has gone longest without exploring; the board dispatcher spawns the PO
  (and ONLY this spawn also gets the playwright MCP — see
  ``AgentOrchestrator._is_dogfood_spawn``), who walks the project's live
  surfaces and calls ``propose_friction_fixes`` exactly once. Approved items
  materialize into BACKLOG only via the CEO's per-item approve
  (``DogfoodService``) — this engine never starts anything.
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
from roboco.services.task import DOGFOOD_SOURCE, TaskCreateRequest, get_task_service

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import ProjectTable, TaskTable
    from roboco.services.task import TaskService

_EXPLORATION_TITLE = "Dogfood walk"
_EXPLORATION_DESCRIPTION = (
    "Walk the target project's live surfaces as a real user would — the "
    "panel via Playwright, the docs site, the Telegram flow when reachable "
    "— and file evidence-backed UX friction. Propose up to "
    f"{PROGRAMS['dogfood'].max_items_per_cycle} items via "
    "propose_friction_fixes() — each item is reviewed and approved/rejected "
    "individually by the CEO; nothing here auto-starts."
)


class DogfoodEngine(BaseService):
    """Originate ONE held dogfood-exploration cycle for the Product Owner."""

    service_name = "dogfood_engine"

    async def run_cycle(self) -> TaskTable | None:
        """Originate one held exploration task, or None (no-op).

        No-ops when the program isn't armed, a cycle is already open, or no
        project has opted in (``projects.board_programs`` contains
        ``"dogfood"``). Never authors content itself — the Product Owner
        does, via ``propose_friction_fixes`` once spawned by the board
        dispatcher. Called from BOTH triggers (the release-publish hook and
        a CEO "run now") via ``BoardProgramEngine.open_program_cycle`` —
        see this module's docstring.
        """
        if not await program_armed(self.session, "dogfood"):
            return None
        task_svc = get_task_service(self.session)
        if await task_svc.list_open_dogfood_cycles():
            return None  # one open cycle at a time
        projects = await get_board_program_engine(self.session).opted_in_projects(
            PROGRAMS["dogfood"]
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
        ``SpackleEngine._originate``."""
        target = await pick_rotation_target(
            self.session, projects, source=DOGFOOD_SOURCE
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
                    "propose_friction_fixes() is called once with 1-"
                    f"{PROGRAMS['dogfood'].max_items_per_cycle} "
                    "evidence-backed friction item drafts",
                ],
                team=Team.BOARD,
                assigned_to=_foundation.AGENTS["product-owner"].uuid,
                created_by=_foundation.AGENTS["system"].uuid,
                task_type=TaskType.ADMINISTRATIVE,
                nature=TaskNature.NON_TECHNICAL,
                estimated_complexity=Complexity.LOW,
                project_id=cast("UUID", target.id),
                status=TaskStatus.PENDING,
                source=DOGFOOD_SOURCE,
                confirmed_by_human=False,  # HELD; board-dispatched, not delivery
            )
        )
        await self.session.flush()
        self.log.info(
            "dogfood exploration cycle opened (Product Owner)",
            task_id=str(task.id),
            project_slug=target.slug,
        )
        return task


def get_dogfood_engine(session: AsyncSession) -> DogfoodEngine:
    """Build a DogfoodEngine for ``session``."""
    return DogfoodEngine(session)
