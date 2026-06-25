"""Dependency-update bot engine — dormant by default.

Mirrors the self-heal / CI-watch engines: for each opted-in project (one with a
``dep_update_command``), probe whether a dependency upgrade would change the
lockfiles (read-only, in a throwaway clone) and, if so, open one
"update dependencies" task into that project's lifecycle and STOP. Conservative:

* **Default OFF** (``dep_update_enabled``) — the loop never starts.
* **Never self-deploys** — it only OPENS a task; the upgrade still ships through
  the normal gates (dev -> QA -> PR review -> the CEO's merge).
* **Bounded + deduped per repo** — at most one open dep_update task per repo
  (keyed on ``git_url``), plus per-cycle and rolling open-task caps.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from roboco.config import settings
from roboco.foundation import identity as _foundation
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType, Team
from roboco.services.base import BaseService
from roboco.services.task import (
    DEP_UPDATE_SOURCE,
    TaskCreateRequest,
    TaskService,
    get_task_service,
)
from roboco.services.workspace import get_workspace_service

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import TaskTable


class DepUpdateEngine(BaseService):
    """Open an update-dependencies task per opted-in project with updates available."""

    service_name = "dep_update_engine"

    def __init__(self, session: AsyncSession, workspace: Any | None = None) -> None:
        super().__init__(session)
        self._workspace = workspace or get_workspace_service(session)

    async def run_cycle(self, projects: list[Any]) -> list[TaskTable]:
        """Probe each opted-in project and open a dep-update task when due (bounded).

        No-op unless ``dep_update_enabled``. Returns the tasks it opened. Flushes;
        the caller (the orchestrator loop) owns the commit. Never starts /
        approves / merges / deploys.
        """
        if not settings.dep_update_enabled:
            return []
        task_svc = get_task_service(self.session)
        open_count = len(await task_svc.list_open_dep_update_tasks())
        created: list[TaskTable] = []
        for project in projects:
            if len(created) >= settings.dep_update_max_per_cycle:
                break
            if open_count >= settings.dep_update_max_open_tasks:
                self.log.info(
                    "dep-update open-task cap reached; not originating",
                    cap=settings.dep_update_max_open_tasks,
                )
                break
            if not await self._eligible(task_svc, project):
                continue
            task = await self._open_task(task_svc, project)
            created.append(task)
            open_count += 1
            self.log.info(
                "dep-update task opened",
                task_id=str(task.id),
                project=str(getattr(project, "slug", "")),
            )
        return created

    async def _eligible(self, task_svc: TaskService, project: Any) -> bool:
        """True when ``project`` has a command, no open task yet, and updates.

        Cheap checks first (command set, per-``git_url`` dedupe), then the
        expensive read-only probe — so a project that's already covered or
        opted out never pays for a clone.
        """
        if not str(getattr(project, "dep_update_command", None) or "").strip():
            return False
        if getattr(project, "id", None) is None:
            return False
        if await task_svc.list_open_dep_update_tasks(git_url=project.git_url):
            return False
        return await self._workspace.dry_upgrade_changes_lockfile(project)

    async def _open_task(self, task_svc: TaskService, project: Any) -> TaskTable:
        slug = str(getattr(project, "slug", "") or "")
        return await task_svc.create(
            TaskCreateRequest(
                title=f"Update dependencies on {slug}",
                description=(
                    "Dependency updates are available for this project.\n\n"
                    "Upgrade the dependencies to their latest compatible versions, "
                    "refresh the lockfile(s), and make sure the full gate passes "
                    "with no behavioural breakage. This task was opened "
                    "automatically by the dependency-update bot and is READY TO "
                    "START NOW — no approval needed. It still ships through the "
                    "normal gates (QA, PR review, and the CEO's merge)."
                ),
                acceptance_criteria=[
                    "Dependencies are upgraded to latest compatible and the "
                    "lockfile(s) are refreshed",
                    "The full quality gate passes with no behavioural regression",
                ],
                team=Team.MAIN_PM,
                assigned_to=_foundation.AGENTS["main-pm"].uuid,
                created_by=_foundation.AGENTS["system"].uuid,
                task_type=TaskType.CODE,
                nature=TaskNature.TECHNICAL,
                estimated_complexity=Complexity.MEDIUM,
                project_id=cast("UUID", project.id),
                status=TaskStatus.PENDING,
                source=DEP_UPDATE_SOURCE,
                confirmed_by_human=True,
            )
        )


def get_dep_update_engine(
    session: AsyncSession, workspace: Any | None = None
) -> DepUpdateEngine:
    """Construct a DepUpdateEngine bound to ``session`` (optionally a test probe)."""
    return DepUpdateEngine(session, workspace=workspace)
