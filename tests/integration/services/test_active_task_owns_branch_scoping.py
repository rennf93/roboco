"""active_task_owns_branch must be scoped to the polled project.

The internal-PR reviewer (orchestrator) calls this to skip the org's own
in-flight integration PRs — a PR on project A's repo is "ours" only if a
non-terminal task ON PROJECT A owns its head branch. The query was unscoped
(``WHERE branch_name = ?``), so a cross-project branch_name collision (two
tasks sharing an 8-char-UUID-prefix branch on different projects) made it
match the WRONG project's task — project A's leftover PR was skipped because
project B happened to have an active task with the same branch_name.

Scoping by ``project_id`` is correct for both single-project tasks and
MegaTask multi-repo batches: each root-subtask carries its own ``project_id``
matching its own repo, so a branch on project A's repo is owned only by a
task whose ``project_id == A``.
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


async def _seed_project(db: AsyncSession, slug: str) -> ProjectTable:
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
        git_url=f"https://github.com/rennf93/{slug}",
        assigned_cell=Team.BACKEND,
        created_by=SYSTEM_UUID,
    )
    db.add(project)
    await db.flush()
    return project


def _task(project_id, *, branch: str, status: TaskStatus) -> TaskTable:
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
            _task(proj_a.id, branch=_BRANCH, status=TaskStatus.IN_PROGRESS),
            _task(proj_b.id, branch=_BRANCH, status=TaskStatus.COMPLETED),
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
async def test_empty_branch_never_owned(db_session: AsyncSession) -> None:
    proj = await _seed_project(db_session, "gca-collide-empty")
    svc = get_task_service(db_session)
    assert await svc.active_task_owns_branch("", cast("UUID", proj.id)) is False
