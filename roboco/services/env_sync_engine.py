"""Env-sync engine — cascade prod→…→head so dev never falls behind prod.

Dormant by default (``env_sync_enabled``). For each opted-in project (a declared
env ladder + a git token) it cascades the upper rungs into the lower ones top-down
via GitHub's merges API: a clean merge auto-pushes to the lower rung, and a
conflict opens ONE sync PR (upper→lower) and stops that project's cascade for the
cycle. Like the other background engines it is conservative:

* **Default OFF** (``env_sync_enabled``) — the orchestrator loop never starts.
* **Never pushes prod** — the cascade's lower/target rung is never prod by
  construction (``ladder_pairs`` yields ``(upper, lower)`` with lower ∈ the
  non-prod rungs), so "only the CEO merges master" holds.
* **Bounded + deduped per repo** — at most one open env_sync task per repo (a
  conflict stops the cascade at that rung), plus per-cycle and rolling caps.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from roboco.config import settings
from roboco.foundation import identity as _foundation
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType, Team
from roboco.models.env_branches import ladder_pairs
from roboco.services.base import BaseService
from roboco.services.git import get_git_service
from roboco.services.task import (
    ENV_SYNC_SOURCE,
    TaskCreateRequest,
    TaskService,
    get_task_service,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import TaskTable


class EnvSyncEngine(BaseService):
    """Cascade the env ladder prod→…→head so dev never falls behind prod."""

    service_name = "env_sync_engine"

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def run_cycle(self, projects: list[Any]) -> list[TaskTable]:
        """Cascade each opted-in project's ladder; return conflict-PR tasks opened.

        No-op unless ``env_sync_enabled``. Per-cycle + rolling caps; one open
        env_sync task per repo (a conflict pauses the cascade at that rung).
        Never pushes prod. Flushes; the caller (the orchestrator loop) owns the
        commit.
        """
        if not settings.env_sync_enabled:
            return []
        task_svc = get_task_service(self.session)
        open_count = len(await task_svc.list_open_env_sync_tasks())
        created: list[TaskTable] = []
        for project in projects:
            if len(created) >= settings.env_sync_max_per_cycle:
                break
            if open_count >= settings.env_sync_max_open_tasks:
                self.log.info(
                    "env-sync open-task cap reached; not cascading",
                    cap=settings.env_sync_max_open_tasks,
                )
                break
            if not await self._should_sync(task_svc, project):
                continue
            task = await self._cascade_project(project)
            if task is not None:
                created.append(task)
                open_count += 1
        return created

    async def _should_sync(self, task_svc: TaskService, project: Any) -> bool:
        """True when ``project`` is opted in and not already being synced.

        Opt-in = a declared env ladder (so there are pairs to cascade) + a git
        token (the merges API needs a PAT). A degenerate ladder (head==prod)
        has no pairs → skip. Dedup is per repo: a project with an open env_sync
        task has its cascade paused at a conflicted rung, so skip it until the
        sync PR resolves.
        """
        if project is None or getattr(project, "id", None) is None:
            return False
        if not getattr(project, "has_git_token", False):
            return False
        if not ladder_pairs(project):
            return False
        existing = await task_svc.list_open_env_sync_tasks(
            git_url=getattr(project, "git_url", None)
        )
        return not existing

    async def _cascade_project(self, project: Any) -> TaskTable | None:
        """Cascade one project top→down; on a conflict open one sync PR + task.

        Clean / already-ancestor steps continue down the ladder. A missing ref
        (misconfigured ladder or API error) skips the project without opening a
        PR. A conflict opens a sync PR and a tracked task, then stops — never
        cascade a dirty merge downward.
        """
        slug = str(getattr(project, "slug", "") or "")
        git = get_git_service(self.session)
        for upper, lower in ladder_pairs(project):
            pair = f"{upper.branch}→{lower.branch}"
            result = await git.sync_env_branch(slug, lower.branch, upper.branch)
            status = result.get("status")
            if status in ("merged", "already_ancestor"):
                self.log.info("env-sync step", project=slug, pair=pair, status=status)
                continue
            if status == "missing_ref":
                self.log.warning(
                    "env-sync step missing ref; skipping project",
                    project=slug,
                    pair=pair,
                )
                return None
            # conflict: open a sync PR + tracked task, stop the cascade.
            pr = await git.open_sync_pr(
                slug,
                upper.branch,
                lower.branch,
                body=(
                    f"The env-sync cascade could not merge `{upper.branch}` "
                    f"into `{lower.branch}` cleanly (non-fast-forward). Resolve "
                    f"the conflict and merge this PR so the cascade can resume.\n\n"
                    f"Opened automatically by the env-sync loop."
                ),
            )
            if pr is None:
                self.log.warning(
                    "env-sync conflict but sync PR open failed; skipping project",
                    project=slug,
                    pair=pair,
                )
                return None
            task = await self._open_sync_task(project, upper.branch, lower.branch, pr)
            self.log.info(
                "env-sync conflict; sync PR opened",
                project=slug,
                pair=pair,
                pr=pr["number"],
            )
            return task
        return None

    async def _open_sync_task(
        self,
        project: Any,
        upper_branch: str,
        lower_branch: str,
        pr: dict[str, Any],
    ) -> TaskTable:
        """Open ONE PENDING, dispatchable coordination task tracking the sync PR."""
        task_svc = get_task_service(self.session)
        slug = str(getattr(project, "slug", "") or "")
        return await task_svc.create(
            TaskCreateRequest(
                title=(
                    f"env-sync: resolve {upper_branch}→{lower_branch} conflict "
                    f"on {slug} (PR #{pr['number']})"
                ),
                description=(
                    f"The env-sync cascade could not merge `{upper_branch}` into "
                    f"`{lower_branch}` cleanly (conflict). A sync PR was opened:\n"
                    f"{pr['url']}\n\n"
                    "Resolve the conflict and merge the PR so the cascade can "
                    "resume. This is a Main-PM coordination root: decompose the "
                    "resolution and delegate the code work to a cell dev — the "
                    "Main PM does not resolve it directly. Opened automatically by "
                    "the env-sync loop; it still ships through the normal gates "
                    "(QA, PR review, and the CEO's merge)."
                ),
                acceptance_criteria=[
                    f"The sync PR {pr['url']} is merged (conflict resolved)",
                    f"`{lower_branch}` is not behind `{upper_branch}`",
                ],
                team=Team.MAIN_PM,
                assigned_to=_foundation.AGENTS["main-pm"].uuid,
                created_by=_foundation.AGENTS["system"].uuid,
                task_type=TaskType.PLANNING,
                nature=TaskNature.TECHNICAL,
                estimated_complexity=Complexity.MEDIUM,
                project_id=cast("UUID", project.id),
                status=TaskStatus.PENDING,
                source=ENV_SYNC_SOURCE,
                confirmed_by_human=True,
            )
        )


def get_env_sync_engine(session: AsyncSession) -> EnvSyncEngine:
    """Construct an EnvSyncEngine bound to ``session``."""
    return EnvSyncEngine(session)
