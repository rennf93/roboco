"""active_task_owns_branch must be scoped to the polled project's REPO.

The inbound-PR reviewer (orchestrator) calls this to skip the org's own
in-flight integration PRs. Scope is the repo (every project sharing the
polled project's ``git_url``), not one project: the poll collapses a
monorepo's cell-projects to one canonical project, so a fleet PR whose
owning task lives on a sibling cell-project must still count as ours
(2026-07-23: a GitHub-App-authored dev-stream PR was ingested as external_pr
because the canonical project's id didn't match the owning cell's).

Cross-REPO branch_name collisions stay excluded — the original bug this file
pinned: an unscoped query (``WHERE branch_name = ?``) matched the WRONG
repo's task on an 8-char-UUID-prefix collision and false-skipped a real PR.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

import pytest
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.foundation import identity as _foundation
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    Complexity,
    TaskNature,
    TaskStatus,
    TaskType,
    Team,
)
from roboco.services.task import get_task_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
_BRANCH = "feature/backend/collide001"


async def _seed_project(
    db: AsyncSession, slug: str, git_url: str | None = None
) -> ProjectTable:
    if await db.get(AgentTable, SYSTEM_UUID) is None:
        db.add(
            AgentTable(
                id=SYSTEM_UUID,
                name="System",
                slug=f"system-{uuid4().hex[:8]}",
                role=AgentRole.SYSTEM,
                team=None,
                status=AgentStatus.ACTIVE,
                model_config={},
                system_prompt="x",
                capabilities=[],
                permissions={},
                metrics={},
            )
        )
        await db.flush()
    project = ProjectTable(
        id=uuid4(),
        name=slug,
        slug=slug,
        git_url=git_url or f"https://github.com/rennf93/{slug}",
        assigned_cell=Team.BACKEND,
        created_by=SYSTEM_UUID,
    )
    db.add(project)
    await db.flush()
    return project


def _task(project_id: UUID, *, branch: str, status: TaskStatus) -> TaskTable:
    return TaskTable(
        id=uuid4(),
        title=f"task {branch}",
        description="x",
        acceptance_criteria=["criterion"],
        status=status,
        priority=2,
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=project_id,
        branch_name=branch,
        created_by=SYSTEM_UUID,
        team=Team.BACKEND,
        dependency_ids=[],
        blocker_ids=[],
        sequence=0,
        estimated_complexity=Complexity.MEDIUM,
    )


@pytest.mark.asyncio
async def test_branch_owned_only_by_its_own_project(db_session: AsyncSession) -> None:
    """Project A has an ACTIVE task on ``_BRANCH``; project B has a COMPLETED
    task with the SAME ``_BRANCH``. Asking for project B must NOT match
    project A's active task (the unscoped query did). Asking for project A
    matches its own active task."""
    proj_a = await _seed_project(db_session, "gca-collide-a")
    proj_b = await _seed_project(db_session, "gca-collide-b")
    db_session.add_all(
        [
            _task(
                cast("UUID", proj_a.id), branch=_BRANCH, status=TaskStatus.IN_PROGRESS
            ),
            _task(cast("UUID", proj_b.id), branch=_BRANCH, status=TaskStatus.COMPLETED),
        ]
    )
    await db_session.flush()
    svc = get_task_service(db_session)

    # The active task on project A owns it for project A.
    assert await svc.active_task_owns_branch(_BRANCH, cast("UUID", proj_a.id)) is True
    # Project B's task is terminal AND the only active owner is on project A —
    # the unscoped query would wrongly return True here (matching A's task).
    assert await svc.active_task_owns_branch(_BRANCH, cast("UUID", proj_b.id)) is False


@pytest.mark.asyncio
async def test_monorepo_sibling_project_ownership_counts(
    db_session: AsyncSession,
) -> None:
    """Two cell-projects share one git_url (monorepo). The active task lives
    on the FRONTEND cell; the poll queries with the CANONICAL sibling's id.
    Repo-scoping must still recognize the branch as ours — the 2026-07-23
    incident shape."""
    repo = "https://github.com/rennf93/gca-mono"
    canonical = await _seed_project(db_session, "gca-mono-api", git_url=repo)
    frontend = await _seed_project(db_session, "gca-mono-panel", git_url=repo)
    branch = "feature/frontend/mono0001--child001"
    db_session.add(
        _task(cast("UUID", frontend.id), branch=branch, status=TaskStatus.IN_PROGRESS)
    )
    await db_session.flush()
    svc = get_task_service(db_session)

    assert await svc.active_task_owns_branch(branch, cast("UUID", canonical.id)) is True


@pytest.mark.asyncio
async def test_empty_branch_never_owned(db_session: AsyncSession) -> None:
    proj = await _seed_project(db_session, "gca-collide-empty")
    svc = get_task_service(db_session)
    assert await svc.active_task_owns_branch("", cast("UUID", proj.id)) is False
