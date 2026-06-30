"""Multi-repo CI-watch engine — dormant by default.

The fan-out generalization of the self-heal engine: instead of RoboCo's single
own repo, it watches EVERY project the operator opted into (``ci_watch_enabled``)
and, when one's CI is red on its default branch, opens one fix task into that
project's delivery lifecycle and STOPS. Like self-heal it is conservative:

* **Default OFF** (``ci_watch_enabled``) — the orchestrator loop never starts.
* **Never self-deploys** — it only OPENS a fix task; the fix still ships through
  the normal gates (dev -> QA -> PR review -> the CEO's merge). The engine never
  starts / approves / merges / deploys.
* **Bounded + deduped per (repo, workflow)** — at most one open ci_watch task
  per ``(git_url, effective workflow)``: a same-workflow monorepo (several
  cell-projects on one repo) shares one fix task, but two RED workflows of one
  repo each get their own (#44). Plus per-cycle and rolling open-task caps.

Reuses the hardened per-project CI lookup via ``MultiProjectCITelemetrySource``;
the single-repo self-heal path is untouched.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from roboco.config import settings
from roboco.foundation import identity as _foundation
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType, Team
from roboco.services.base import BaseService
from roboco.services.notification import NotificationService
from roboco.services.task import (
    CI_WATCH_SOURCE,
    TaskCreateRequest,
    TaskService,
    get_task_service,
)
from roboco.services.telemetry.source import get_multi_ci_telemetry_source

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import TaskTable


def _cell_pm_slug_for(team: Any) -> str | None:
    """The cell PM slug owning ``team`` (e.g. Team.BACKEND → 'be-pm'), or None."""
    for row in _foundation.AGENTS.values():
        if row.role == _foundation.Role.CELL_PM and row.team == team:
            return row.slug
    return None


class CiWatchEngine(BaseService):
    """Open a fix task for each opted-in project whose CI is red. Never merges."""

    service_name = "ci_watch_engine"

    def __init__(self, session: AsyncSession, source: Any | None = None) -> None:
        super().__init__(session)
        self._source = source or get_multi_ci_telemetry_source(session)

    async def run_cycle(self, projects: list[Any]) -> list[TaskTable]:
        """Assess the watch set and open a fix task per red repo (bounded).

        No-op unless ``ci_watch_enabled``. Returns the tasks it opened. Flushes;
        the caller (the orchestrator loop) owns the commit. Never starts /
        approves / merges / deploys.
        """
        if not settings.ci_watch_enabled:
            return []
        samples = await self._source.fetch(projects)
        breaches = [s for s in samples if s.is_breach]
        if not breaches:
            return []
        by_slug = {str(getattr(p, "slug", "")): p for p in projects}
        return await self._originate(breaches, by_slug)

    async def _originate(
        self, breaches: list[Any], by_slug: dict[str, Any]
    ) -> list[TaskTable]:
        """Open one ci_watch fix task per NEW red repo, bounded. Returns them."""
        task_svc = get_task_service(self.session)
        open_count = len(await task_svc.list_open_ci_watch_tasks())
        created: list[TaskTable] = []
        for sample in breaches:
            if len(created) >= settings.ci_watch_max_per_cycle:
                break
            if open_count >= settings.ci_watch_max_open_tasks:
                self.log.info(
                    "ci-watch open-task cap reached; not originating",
                    cap=settings.ci_watch_max_open_tasks,
                )
                break
            project = by_slug.get(sample.repo_hint)
            if not await self._should_open(task_svc, project):
                continue
            task = await self._open_fix_task(task_svc, project, sample)
            created.append(task)
            open_count += 1
            self.log.info(
                "ci-watch fix task opened",
                task_id=str(task.id),
                repo=sample.repo_hint,
            )
            await self._notify_cell_pm(project, sample)
        return created

    async def _notify_cell_pm(self, project: Any, sample: Any) -> None:
        """Notify the red project's cell PM that a fix task was opened.

        Routed to the project's own cell PM (not the CEO — a delivery/client
        repo's red CI is a cell concern), once per project per cycle (the engine
        opens at most one task per repo per cycle). Best-effort: a notification
        failure never rolls back the origination.
        """
        team = getattr(project, "assigned_cell", None)
        pm_slug = _cell_pm_slug_for(team) if team is not None else None
        if not pm_slug:
            return
        try:
            await NotificationService().send_ack_notification(
                from_agent="system",
                to_agent=pm_slug,
                body=(
                    f"[ci-watch] CI is red on {sample.repo_hint}. A fix task was "
                    f"opened automatically and is ready to start.\n\n{sample.detail}"
                ),
            )
        except Exception as exc:
            self.log.warning(
                "ci-watch cell-PM notify failed (best-effort)",
                repo=sample.repo_hint,
                error=str(exc),
            )

    async def _should_open(self, task_svc: TaskService, project: Any) -> bool:
        """True when ``project`` resolves and has no open ci_watch task yet.

        Dedupe is per ``(git_url, effective workflow)`` so a monorepo with
        several cell-projects on ONE repo still gets a single open fix task per
        workflow — a same-workflow monorepo collapses to one task, but two RED
        workflows of one repo each get their own (#44). The effective workflow is
        ``ci_watch_workflow`` falling back to the configured default.
        """
        if project is None or getattr(project, "id", None) is None:
            return False
        workflow = (
            str(
                getattr(project, "ci_watch_workflow", None)
                or settings.ci_watch_default_workflow
            ).strip()
            or None
        )
        existing = await task_svc.list_open_ci_watch_tasks(
            git_url=project.git_url, workflow=workflow
        )
        return not existing

    async def _open_fix_task(
        self, task_svc: TaskService, project: Any, sample: Any
    ) -> TaskTable:
        slug = str(getattr(project, "slug", "") or sample.repo_hint)
        return await task_svc.create(
            TaskCreateRequest(
                title=f"CI-watch: fix the CI regression on {slug}",
                description=(
                    f"This project's CI is red on its default branch.\n\n"
                    f"{sample.detail}\n\n"
                    f"Evidence: {sample.raw_ref}\n\n"
                    "This is a Main-PM coordination root: decompose the fix and "
                    "delegate the code work to a cell dev — the Main PM does not "
                    "write the fix itself. This task was opened automatically by "
                    "the CI-watch loop and is READY TO START NOW — no approval "
                    "needed; plan the fix and delegate it. It still ships through "
                    "the normal gates (QA, PR review, and the CEO's merge)."
                ),
                acceptance_criteria=[
                    "The CI regression is decomposed into a code-fix subtask "
                    "delegated to a cell developer",
                    f"CI on {slug}'s default branch is green again and the fix "
                    "merged through the normal gates",
                ],
                team=Team.MAIN_PM,
                assigned_to=_foundation.AGENTS["main-pm"].uuid,
                created_by=_foundation.AGENTS["system"].uuid,
                task_type=TaskType.PLANNING,
                nature=TaskNature.TECHNICAL,
                estimated_complexity=Complexity.MEDIUM,
                project_id=cast("UUID", project.id),
                status=TaskStatus.PENDING,
                source=CI_WATCH_SOURCE,
                confirmed_by_human=True,
            )
        )


def get_ci_watch_engine(
    session: AsyncSession, source: Any | None = None
) -> CiWatchEngine:
    """Construct a CiWatchEngine bound to ``session`` (optionally a test source)."""
    return CiWatchEngine(session, source=source)
