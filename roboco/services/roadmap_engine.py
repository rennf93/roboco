"""RoadmapEngine — weekly board roadmap exploration, held for the CEO.

Mirrors the ReleaseManagerEngine "detect -> originate a CEO-gated artifact ->
hold" shape, but the artifact here is a themed cycle the Product Owner
AUTHORS rather than a report the engine assembles itself:

* **Default OFF.** ``roadmap_engine_enabled`` is False, so the loop never
  runs and nothing is originated.
* **One open cycle at a time.** Dedup by ``source=board_roadmap`` non-terminal
  tasks — a new cycle is never originated while one is still awaiting the
  Product Owner's authoring or the CEO's per-item decisions.
* **The engine never authors content.** It opens ONE held, PENDING
  exploration task assigned to the Product Owner (``Team.BOARD``,
  ``confirmed_by_human=False``); the existing board one-shot dispatch spawns
  the PO, who explores and calls ``propose_roadmap`` exactly once. Approved
  items materialize into BACKLOG only via the CEO's per-item approve
  (``RoadmapService``) — this engine never starts anything.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from roboco.config import settings
from roboco.foundation import identity as _foundation
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType, Team
from roboco.services.base import BaseService
from roboco.services.project import get_project_service
from roboco.services.task import ROADMAP_SOURCE, TaskCreateRequest, get_task_service

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import ProjectTable, TaskTable
    from roboco.services.task import TaskService

_EXPLORATION_TITLE = "Roadmap exploration cycle"
_EXPLORATION_DESCRIPTION = (
    "Explore the company's projects, the charter, recent releases, and "
    "metrics, then propose one themed cycle of roadmap item drafts via "
    "propose_roadmap(). Each item is reviewed and approved/rejected "
    "individually by the CEO in the roadmap queue; nothing here auto-starts "
    "— an approved item lands in BACKLOG for normal PM activation."
)


class RoadmapEngine(BaseService):
    """Originate ONE held roadmap-exploration cycle for the Product Owner."""

    service_name = "roadmap_engine"

    async def run_cycle(self) -> TaskTable | None:
        """Originate one held exploration task, or None (no-op).

        No-ops when the flag is off, a cycle is already open, or the RoboCo
        project isn't resolvable. Never authors content itself — the Product
        Owner does, via ``propose_roadmap`` once spawned by the board
        dispatcher.
        """
        if not settings.roadmap_engine_enabled:
            return None
        task_svc = get_task_service(self.session)
        if await task_svc.list_open_roadmap_cycles():
            return None  # one open cycle at a time
        project = await self._roboco_project()
        if project is None or project.id is None:
            self.log.warning(
                "roadmap-engine: RoboCo project not resolvable; skipping",
            )
            return None
        return await self._originate(task_svc, cast("UUID", project.id))

    async def _roboco_project(self) -> ProjectTable | None:
        slug = (settings.self_heal_project_slug or "roboco-api").strip()
        return await get_project_service(self.session).get_by_slug(slug)

    async def _originate(self, task_svc: TaskService, project_id: UUID) -> TaskTable:
        """Open ONE PENDING, HELD exploration task assigned to the Product Owner."""
        task = await task_svc.create(
            TaskCreateRequest(
                title=_EXPLORATION_TITLE,
                description=_EXPLORATION_DESCRIPTION,
                acceptance_criteria=[
                    f"propose_roadmap() is called once with a themed cycle of "
                    f"{settings.roadmap_min_items_per_cycle}-"
                    f"{settings.roadmap_max_items_per_cycle} item drafts",
                ],
                team=Team.BOARD,
                assigned_to=_foundation.AGENTS["product-owner"].uuid,
                created_by=_foundation.AGENTS["system"].uuid,
                task_type=TaskType.ADMINISTRATIVE,
                nature=TaskNature.NON_TECHNICAL,
                estimated_complexity=Complexity.LOW,
                project_id=project_id,
                status=TaskStatus.PENDING,
                source=ROADMAP_SOURCE,
                confirmed_by_human=False,  # HELD; board-dispatched, not delivery
            )
        )
        await self.session.flush()
        self.log.info(
            "roadmap exploration cycle opened (Product Owner)", task_id=str(task.id)
        )
        return task


def get_roadmap_engine(session: AsyncSession) -> RoadmapEngine:
    """Build a RoadmapEngine for ``session``."""
    return RoadmapEngine(session)
