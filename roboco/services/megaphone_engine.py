"""MegaphoneEngine — Megaphone (Board Program), org-scoped.

Mirrors ``PeriscopeEngine``'s org-scoped "detect -> originate a CEO-gated
exploration -> hold" shape, but the materializer is the EXISTING X held-draft
queue (``XEngine.materialize_editorial_post`` / ``XPostService``), not a
report: the HoM's standing editorial calendar beyond release posts and
feature spotlights — dev-log threads ("what the fleet shipped this week"),
behind-the-scenes posts, changelog highlights (spec §4). Zero new approval
surface — the CEO reviews these in the same X post queue release/spotlight
drafts already land in.

* **No master enable flag.** Armed via ``roboco.services.board_programs.
  program_armed`` — the settings-store ``board_program.megaphone.enabled``
  key is the ONLY arming path; off by default like every other program.
* **Also gated on X credentials.** Drafting a post nobody can ever post is
  pointless — mirrors ``XEngine.open_feature_spotlight_exploration``'s
  identical guard (reuses ``XCredentialsService.has_credentials()``, the
  cheap boolean check rather than building a full client).
* **One open cycle at a time.** Dedup by ``source=board_megaphone``
  non-terminal tasks.
* **The engine never authors content.** It opens ONE held, PENDING
  exploration task assigned to the Head of Marketing (``Team.BOARD``,
  ``confirmed_by_human=False``); the board dispatcher spawns HoM, who picks
  ONE angle off the server-assembled digest (``digest_context``) and calls
  ``propose_editorial_post`` exactly once, which completes the exploration
  task in the same call (mirrors ``XEngine.materialize_feature_spotlight``'s
  complete-at-propose asymmetry).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

from sqlalchemy import select

from roboco.config import settings
from roboco.foundation import identity as _foundation
from roboco.models.base import Complexity, TaskNature, TaskStatus, TaskType, Team
from roboco.services.base import BaseService
from roboco.services.board_programs import program_armed
from roboco.services.project import get_project_service
from roboco.services.task import MEGAPHONE_SOURCE, TaskCreateRequest, get_task_service
from roboco.services.x_credentials import get_x_credentials_service

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import ProjectTable, TaskTable
    from roboco.services.task import TaskService

_EXPLORATION_TITLE = "Megaphone editorial cycle"
_EXPLORATION_DESCRIPTION = (
    "Pick ONE angle from the shipped-this-week digest in your briefing and "
    "file ONE post for the CEO's X queue via propose_editorial_post(). This "
    "is the standing editorial calendar, beyond release posts and feature "
    "spotlights: a dev-log thread on what the fleet shipped this week, a "
    "behind-the-scenes note, or a changelog highlight."
)

# Completed-task digest cap (spec §4) — bounds the section regardless of how
# busy the week was.
_DIGEST_TASK_LIMIT = 15


class MegaphoneEngine(BaseService):
    """Originate ONE held Megaphone-exploration cycle for the Head of Marketing."""

    service_name = "megaphone_engine"

    async def run_cycle(self) -> TaskTable | None:
        """Originate one held exploration task, or None (no-op).

        No-ops when the program isn't armed, no X credentials are configured
        (drafting content nobody can ever post is pointless), a cycle is
        already open, or the RoboCo project (the task's required FK anchor)
        isn't resolvable. Never authors content itself — the Head of
        Marketing does, via ``propose_editorial_post`` once spawned by the
        board dispatcher.
        """
        if not await program_armed(self.session, "megaphone"):
            return None
        if not await get_x_credentials_service(self.session).has_credentials():
            return None
        task_svc = get_task_service(self.session)
        if await task_svc.list_open_megaphone_cycles():
            return None  # one open cycle at a time
        project = await self._roboco_project()
        if project is None or project.id is None:
            self.log.warning(
                "megaphone-engine: RoboCo project not resolvable; skipping"
            )
            return None
        return await self._originate(task_svc, cast("UUID", project.id))

    async def _roboco_project(self) -> ProjectTable | None:
        slug = (settings.self_heal_project_slug or "roboco-api").strip()
        return await get_project_service(self.session).get_by_slug(slug)

    async def _originate(self, task_svc: TaskService, project_id: UUID) -> TaskTable:
        """Open ONE PENDING, HELD exploration task assigned to the Head of
        Marketing."""
        task = await task_svc.create(
            TaskCreateRequest(
                title=_EXPLORATION_TITLE,
                description=_EXPLORATION_DESCRIPTION,
                acceptance_criteria=[
                    "propose_editorial_post() is called once with an angle, "
                    "a <=280-char body, and a rationale"
                ],
                team=Team.BOARD,
                assigned_to=_foundation.AGENTS["head-marketing"].uuid,
                created_by=_foundation.AGENTS["system"].uuid,
                task_type=TaskType.ADMINISTRATIVE,
                nature=TaskNature.NON_TECHNICAL,
                estimated_complexity=Complexity.LOW,
                project_id=project_id,
                status=TaskStatus.PENDING,
                source=MEGAPHONE_SOURCE,
                confirmed_by_human=False,  # HELD; board-dispatched, not delivery
            )
        )
        await self.session.flush()
        self.log.info(
            "megaphone exploration cycle opened (Head of Marketing)",
            task_id=str(task.id),
        )
        return task

    async def digest_context(self) -> str:
        """Server-assembled context for the exploration prompt: completed
        tasks this week (title/project/team, capped) plus the CHANGELOG's
        Unreleased bullets, when cheaply readable. Best-effort throughout — a
        read failure degrades that section to an explicit "unavailable" line
        rather than blocking the spawn (spec: "skip cleanly if not — say so")."""
        shipped = await self._shipped_this_week()
        changelog = await self._unreleased_changelog()
        lines = ["Completed this week:", *(shipped or ["- (nothing completed)"])]
        lines.append("")
        lines.append("CHANGELOG.md Unreleased section:")
        lines.append(changelog or "(not available this cycle)")
        return "\n".join(lines)

    async def _shipped_this_week(self) -> list[str]:
        from roboco.db.tables import ProjectTable, TaskTable

        cutoff = datetime.now(UTC) - timedelta(days=7)
        result = await self.session.execute(
            select(TaskTable.title, TaskTable.team, ProjectTable.name)
            .outerjoin(ProjectTable, TaskTable.project_id == ProjectTable.id)
            .where(
                TaskTable.status == TaskStatus.COMPLETED,
                TaskTable.completed_at.is_not(None),
                TaskTable.completed_at >= cutoff,
            )
            .order_by(TaskTable.completed_at.desc())
            .limit(_DIGEST_TASK_LIMIT)
        )
        return [
            f"- {title} ({project_name or 'no project'}, {team.value})"
            for title, team, project_name in result.all()
        ]

    async def _unreleased_changelog(self) -> str:
        """The curated ``## [Unreleased]`` body from the RoboCo project's read
        clone; "" when the file/section is absent, blank, or unreadable —
        never raises (caller renders the empty case explicitly)."""
        try:
            from roboco.services.release_readiness import (
                _read_changelog,
                _unreleased_body,
            )
            from roboco.services.workspace import get_workspace_service

            slug = (settings.self_heal_project_slug or "roboco-api").strip()
            root = await get_workspace_service(self.session).ensure_read_clone(slug)
            return _unreleased_body(_read_changelog(Path(root)))
        except Exception as exc:
            self.log.warning(
                "megaphone-engine: changelog read failed (best-effort)",
                error=str(exc),
            )
            return ""


def get_megaphone_engine(session: AsyncSession) -> MegaphoneEngine:
    """Build a MegaphoneEngine for ``session``."""
    return MegaphoneEngine(session)
