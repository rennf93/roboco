"""Shared shipped-work digest helper for board-program exploration prompts.

Assembles a "Completed this week" list (capped) plus the CHANGELOG.md
``## [Unreleased]`` body into a single string, best-effort throughout. A read
failure degrades that section to an explicit "unavailable" line rather than
raising — the caller (an orchestrator wrapper or ``MegaphoneEngine``) renders
the empty case explicitly and never blocks a spawn on a digest failure.

Extracted from ``MegaphoneEngine`` so roadmap, Pest Control, and Spackle
exploration prompts share one assembly path with the Megaphone editorial
cycle. Side-effect-free at module level: the workspace service is imported
lazily inside ``_unreleased_changelog`` so ``roboco/utils/`` stays clear of
service-module imports at import time (per the architectural standard).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select

from roboco.logging import get_logger
from roboco.models.base import TaskStatus

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

# Completed-task digest cap — bounds the section regardless of how busy the
# week was. Preserved verbatim from the original MegaphoneEngine constant.
_DIGEST_TASK_LIMIT = 15


async def shipped_work_digest(session: AsyncSession, roboco_project_slug: str) -> str:
    """Assemble the shipped-this-week digest for a board-program prompt.

    Combines a "Completed this week" bullet list (COMPLETED tasks in the last
    7 days, joined to the project name, capped at ``_DIGEST_TASK_LIMIT``) with
    the CHANGELOG.md ``## [Unreleased]`` body from the RoboCo project's read
    clone. Both halves degrade to explicit lines when empty or unreadable —
    never raises, never returns an empty section.
    """
    shipped = await _shipped_this_week(session)
    changelog = await _unreleased_changelog(session, roboco_project_slug)
    lines = ["Completed this week:", *(shipped or ["- (nothing completed)"])]
    lines.append("")
    lines.append("CHANGELOG.md Unreleased section:")
    lines.append(changelog or "(not available this cycle)")
    return "\n".join(lines)


async def _shipped_this_week(session: AsyncSession) -> list[str]:
    """Formatted bullet lines for COMPLETED tasks in the last 7 days, capped."""
    from roboco.db.tables import ProjectTable, TaskTable  # noqa: PLC0415

    cutoff = datetime.now(UTC) - timedelta(days=7)
    result = await session.execute(
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


async def _unreleased_changelog(session: AsyncSession, roboco_project_slug: str) -> str:
    """The curated ``## [Unreleased]`` body from the RoboCo project's read
    clone; ``""`` when the file/section is absent, blank, or unreadable —
    never raises (caller renders the empty case explicitly)."""
    try:
        from roboco.services.release_readiness import (  # noqa: PLC0415
            _read_changelog,
            _unreleased_body,
        )
        from roboco.services.workspace import get_workspace_service  # noqa: PLC0415

        root = await get_workspace_service(session).ensure_read_clone(
            roboco_project_slug
        )
        return _unreleased_body(_read_changelog(Path(root)))
    except Exception as exc:
        logger.warning(
            "shipped-work-digest: changelog read failed (best-effort)",
            error=str(exc),
        )
        return ""
