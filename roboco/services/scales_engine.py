"""ScalesEngine — Scales (Board Program), org-scoped.

Mirrors ``roboco.services.periscope_engine.PeriscopeEngine``'s org-scope shape
(spec §4: it reviews the LIVE portfolio across every project against the
charter, not one repo, so it needs no per-project opt-in to RUN — but the
exploration task itself still needs a resolvable ``project_id``, the same
``TaskService._require_target_or_umbrella`` FK-anchor invariant roadmap/
periscope/x_feature already carry despite being org-scoped too; it resolves
against the RoboCo project purely as an anchor) — and ``PestControlEngine``'s
server-assembled-evidence shape (the Product Owner cannot run the stale-
backlog aggregate query itself, so this engine gathers it ahead of the spawn).

* **No master enable flag.** Armed via ``roboco.services.board_programs.
  program_armed`` — the settings-store ``board_program.scales.enabled`` key is
  the ONLY arming path; off by default like every other program.
* **One open cycle at a time.** Dedup by ``source=board_scales`` non-terminal
  tasks.
* **The engine never authors content or mutates anything.** It opens ONE
  held, PENDING exploration task assigned to the Product Owner (``Team.
  BOARD``, ``confirmed_by_human=False``); the board dispatcher spawns the PO,
  who reviews the stale-backlog snapshot and calls ``propose_rebalance``
  exactly once. Approved items MUTATE a live task (reprioritize) or cancel it
  — only via the CEO's per-item approve (``ScalesService``) — this engine
  never touches a task's priority or status.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

from roboco.config import settings
from roboco.foundation import identity as _foundation
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType, Team
from roboco.services.base import BaseService
from roboco.services.board_programs import program_armed
from roboco.services.project import get_project_service
from roboco.services.task import SCALES_SOURCE, TaskCreateRequest, get_task_service

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import ProjectTable, TaskTable
    from roboco.services.task import TaskService

_EXPLORATION_TITLE = "Scales portfolio-rebalance cycle"
_EXPLORATION_DESCRIPTION = (
    "Review the live backlog against the company charter and propose "
    "re-prioritizations and cancellations — the org has no other mechanism "
    "that ever retires stale backlog. Propose up to 7 items via "
    "propose_rebalance() — each item is reviewed and approved/rejected "
    "individually by the CEO; nothing here auto-starts, and an approved "
    "item MUTATES the live task (reprioritize) or cancels it, it never "
    "creates a new one."
)

# Stale-backlog snapshot caps — the prompt injection stays bounded regardless
# of how large the backlog has grown.
_STALE_BACKLOG_LIMIT = 15
_STALE_DAYS = 30


class ScalesEngine(BaseService):
    """Originate ONE held Scales-exploration cycle for the Product Owner."""

    service_name = "scales_engine"

    async def run_cycle(self) -> TaskTable | None:
        """Originate one held exploration task, or None (no-op).

        No-ops when the program isn't armed, a cycle is already open, or the
        RoboCo project (the task's required FK anchor) isn't resolvable.
        Never authors content itself — the Product Owner does, via
        ``propose_rebalance`` once spawned by the board dispatcher.
        """
        if not await program_armed(self.session, "scales"):
            return None
        task_svc = get_task_service(self.session)
        if await task_svc.list_open_scales_cycles():
            return None  # one open cycle at a time
        project = await self._roboco_project()
        if project is None or project.id is None:
            self.log.warning("scales-engine: RoboCo project not resolvable; skipping")
            return None
        return await self._originate(task_svc, cast("UUID", project.id))

    async def _roboco_project(self) -> ProjectTable | None:
        slug = (settings.self_heal_project_slug or "roboco-api").strip()
        return await get_project_service(self.session).get_by_slug(slug)

    async def _originate(self, task_svc: TaskService, project_id: UUID) -> TaskTable:
        """Open ONE PENDING, HELD exploration task assigned to the Product
        Owner."""
        task = await task_svc.create(
            TaskCreateRequest(
                title=_EXPLORATION_TITLE,
                description=_EXPLORATION_DESCRIPTION,
                acceptance_criteria=[
                    "propose_rebalance() is called once with 1-7 re-priority/"
                    "cancellation item drafts, each naming a live task"
                ],
                team=Team.BOARD,
                assigned_to=_foundation.AGENTS["product-owner"].uuid,
                created_by=_foundation.AGENTS["system"].uuid,
                task_type=TaskType.ADMINISTRATIVE,
                nature=TaskNature.NON_TECHNICAL,
                estimated_complexity=Complexity.LOW,
                project_id=project_id,
                status=TaskStatus.PENDING,
                source=SCALES_SOURCE,
                confirmed_by_human=False,  # HELD; board-dispatched, not delivery
            )
        )
        await self.session.flush()
        self.log.info(
            "scales exploration cycle opened (Product Owner)", task_id=str(task.id)
        )
        return task

    async def evidence_context(self) -> str:
        """Server-assembled stale-backlog snapshot for the PO's prompt — the
        PO cannot run this aggregate query itself (no SQL tool), so the
        engine gathers it ahead of the spawn (mirrors ``PestControlEngine.
        evidence_context``'s shape). Empty string when nothing is stale."""
        rows = await self._stale_backlog_snapshot()
        if not rows:
            return ""
        lines = [
            f"- {id8} {title!r} ({slug or 'no project'}) — P{priority}, "
            f"{age}d in the backlog"
            for id8, title, slug, priority, age in rows
        ]
        return (
            f"Stale backlog (BACKLOG/PENDING, >= {_STALE_DAYS}d old, oldest "
            f"first, capped {_STALE_BACKLOG_LIMIT}):\n" + "\n".join(lines)
        )

    async def _stale_backlog_snapshot(
        self,
    ) -> list[tuple[str, str, str | None, int, int]]:
        """BACKLOG/PENDING tasks older than ``_STALE_DAYS`` — both statuses
        are, by lifecycle construction, unclaimed (``claim()`` moves a task
        to CLAIMED), so no separate claimant filter is needed."""
        from sqlalchemy import select

        from roboco.db.tables import ProjectTable, TaskTable

        cutoff = datetime.now(UTC) - timedelta(days=_STALE_DAYS)
        result = await self.session.execute(
            select(
                TaskTable.id,
                TaskTable.title,
                ProjectTable.slug,
                TaskTable.priority,
                TaskTable.created_at,
            )
            .join(ProjectTable, TaskTable.project_id == ProjectTable.id, isouter=True)
            .where(
                TaskTable.status.in_([TaskStatus.BACKLOG, TaskStatus.PENDING]),
                TaskTable.created_at <= cutoff,
            )
            .order_by(TaskTable.created_at)
            .limit(_STALE_BACKLOG_LIMIT)
        )
        now = datetime.now(UTC)
        return [
            (str(tid)[:8], title, slug, priority, (now - created_at).days)
            for tid, title, slug, priority, created_at in result.all()
        ]


def get_scales_engine(session: AsyncSession) -> ScalesEngine:
    """Build a ScalesEngine for ``session``."""
    return ScalesEngine(session)
