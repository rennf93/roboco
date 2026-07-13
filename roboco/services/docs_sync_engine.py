"""Docs-divergence sync engine — dormant by default.

On a successful release publish, if ``docs_sync_enabled`` is on, the engine
originates exactly one docs-update task against the ``roboco-website`` project,
carrying the release's CHANGELOG section as the brief plus a pointer to the
divergence checklist. Conservative:

* **Default OFF** (``docs_sync_enabled``) — the engine is never invoked.
* **Never self-deploys** — it only OPENS a task; the docs update still ships
  through the normal gates (dev -> QA -> PR review -> the CEO's merge).
* **Bounded + deduped per release** — at most one open docs_sync task per
  release version, plus a rolling open-task cap.

The release-proposal service calls ``originate_docs_update`` from its publish-
success path; the engine itself has no background loop.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from roboco.config import settings
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType, Team
from roboco.services.base import BaseService
from roboco.services.project import get_project_service
from roboco.services.task import (
    DOCS_SYNC_SOURCE,
    TaskCreateRequest,
    TaskService,
    get_task_service,
)

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import TaskTable


logger = logging.getLogger(__name__)

# Project slug that hosts the public docs site. The engine logs a warning and
# no-ops when this project is not registered.
_DOCS_PROJECT_SLUG = "roboco-website"

# Pointer to the divergence checklist included in every originated task.
_DIVERGENCE_CHECKLIST_POINTER = (
    "Review the divergence checklist in the release readiness report "
    "(docs_drift gaps such as declared-vs-actual agent count and stale "
    "verb-surface tables) and update the public docs at docs.roboco.tech "
    "so they reflect what actually shipped."
)


class DocsSyncEngine(BaseService):
    """Open one docs-update task per published release against roboco-website."""

    service_name = "docs_sync_engine"

    async def originate_docs_update(
        self, version: str, changelog: str
    ) -> TaskTable | None:
        """If enabled, open one docs-update task for this release.

        Returns the created task, or None when disabled, when roboco-website is
        not registered, when the open-task cap is reached, or when a task for
        this version is already open. Flushes; the caller (release_proposal)
        owns the commit. Never starts / approves / merges.
        """
        if not settings.docs_sync_enabled:
            return None

        project = await get_project_service(self.session).get_by_slug(
            _DOCS_PROJECT_SLUG
        )
        if project is None or getattr(project, "id", None) is None:
            logger.warning(
                "docs-sync enabled but project %r is not registered; "
                "skipping docs-update task origination",
                _DOCS_PROJECT_SLUG,
            )
            return None

        task_svc = get_task_service(self.session)
        open_tasks = await task_svc.list_open_docs_sync_tasks()
        open_count = len(open_tasks)
        if open_count >= settings.docs_sync_max_open_tasks:
            self.log.info(
                "docs-sync open-task cap reached; not originating",
                cap=settings.docs_sync_max_open_tasks,
            )
            return None

        if await self._already_open_for_version(task_svc, version):
            self.log.info(
                "docs-sync task already open for version",
                version=version,
            )
            return None

        task = await self._open_task(
            task_svc, cast("UUID", project.id), version, changelog
        )
        self.log.info(
            "docs-sync task opened",
            task_id=str(task.id),
            version=version,
            project=_DOCS_PROJECT_SLUG,
        )
        return task

    async def _already_open_for_version(
        self, task_svc: TaskService, version: str
    ) -> bool:
        """True when a non-terminal docs_sync task already exists for ``version``."""
        existing = await task_svc.list_open_docs_sync_tasks(version=version)
        return bool(existing)

    async def _open_task(
        self,
        task_svc: TaskService,
        project_id: UUID,
        version: str,
        changelog: str,
    ) -> TaskTable:
        task = await task_svc.create(
            TaskCreateRequest(
                title=f"Docs update for release v{version}",
                description=(
                    f"A release ({version}) has published. Update the public "
                    f"docs so they reflect what shipped.\n\n"
                    f"## Release CHANGELOG\n\n{changelog}\n\n"
                    f"## Divergence checklist\n\n{_DIVERGENCE_CHECKLIST_POINTER}\n\n"
                    "This is a Main-PM coordination root: decompose the docs "
                    "update and delegate the code work to a cell dev or "
                    "documenter — the Main PM does not write the update "
                    "itself. This task was opened automatically by the "
                    "docs-sync engine and is READY TO START NOW — no approval "
                    "needed. It still ships through the normal gates (QA, PR "
                    "review, and the CEO's merge)."
                ),
                acceptance_criteria=[
                    "The docs update is decomposed into one or more delivery "
                    "subtasks delegated to a cell",
                    f"docs.roboco.tech accurately reflects the v{version} "
                    "release and the update is merged through the normal gates",
                ],
                team=Team.MAIN_PM,
                assigned_to=_foundation.AGENTS["main-pm"].uuid,
                created_by=_foundation.AGENTS["system"].uuid,
                task_type=TaskType.PLANNING,
                nature=TaskNature.TECHNICAL,
                estimated_complexity=Complexity.MEDIUM,
                project_id=project_id,
                status=TaskStatus.PENDING,
                source=DOCS_SYNC_SOURCE,
                confirmed_by_human=True,
            )
        )
        markers.set_docs_sync_release_version(task, version)
        await self.session.flush()
        return task


def get_docs_sync_engine(session: AsyncSession) -> DocsSyncEngine:
    """Construct a DocsSyncEngine bound to ``session``."""
    return DocsSyncEngine(session)
