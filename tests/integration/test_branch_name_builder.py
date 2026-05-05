"""branch_name builder coverage."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import (
    TaskNature,
    TaskStatus,
    TaskType,
)
from roboco.services.task import TaskService
from roboco.templates.git.branch import (
    BranchNameError,
    build_branch_name,
    get_root_task_id,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def branch_setup(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
    agent = AgentTable(
        id=uuid4(),
        name="Dev",
        slug=f"be-dev-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="B-Proj",
        slug=f"b-proj-{uuid4().hex[:6]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
    )
    db_session.add(project)
    await db_session.flush()

    def _make_task(parent_id=None) -> TaskTable:
        task = TaskTable(
            id=uuid4(),
            title="t",
            description="d",
            acceptance_criteria=["ac"],
            status=TaskStatus.PENDING,
            priority=2,
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            project_id=project.id,
            created_by=agent.id,
            team=Team.BACKEND,
            parent_task_id=parent_id,
        )
        db_session.add(task)
        return task

    yield {
        "svc": TaskService(db_session),
        "agent_id": agent.id,
        "project_id": project.id,
        "make_task": _make_task,
        "db": db_session,
    }


@pytest.mark.asyncio
async def test_build_branch_name_root_task(branch_setup: dict) -> None:
    task = branch_setup["make_task"]()
    await branch_setup["db"].flush()
    branch = await build_branch_name(task.id, "feature", "backend", branch_setup["svc"])
    assert branch.startswith("feature/backend/")
    # Should be 8-char prefix only.
    assert len(branch.split("/")[-1]) == 8


@pytest.mark.asyncio
async def test_build_branch_name_with_parent(branch_setup: dict) -> None:
    parent = branch_setup["make_task"]()
    await branch_setup["db"].flush()
    child = branch_setup["make_task"](parent_id=parent.id)
    await branch_setup["db"].flush()
    branch = await build_branch_name(
        child.id, "feature", "backend", branch_setup["svc"]
    )
    # Format: feature/backend/{parent[:8]}--{child[:8]}
    assert "--" in branch
    parts = branch.split("/")[-1].split("--")
    assert len(parts) == 2


@pytest.mark.asyncio
async def test_build_branch_name_invalid_type_raises(branch_setup: dict) -> None:
    task = branch_setup["make_task"]()
    await branch_setup["db"].flush()
    with pytest.raises(BranchNameError, match="Invalid branch type"):
        await build_branch_name(task.id, "ghost", "backend", branch_setup["svc"])


@pytest.mark.asyncio
async def test_build_branch_name_unknown_task_raises(branch_setup: dict) -> None:
    with pytest.raises(BranchNameError, match="Task not found"):
        await build_branch_name(uuid4(), "feature", "backend", branch_setup["svc"])


@pytest.mark.asyncio
async def test_get_root_task_id_for_root(branch_setup: dict) -> None:
    task = branch_setup["make_task"]()
    await branch_setup["db"].flush()
    root = await get_root_task_id(task.id, branch_setup["svc"])
    assert root == task.id


@pytest.mark.asyncio
async def test_get_root_task_id_walks_up(branch_setup: dict) -> None:
    parent = branch_setup["make_task"]()
    await branch_setup["db"].flush()
    child = branch_setup["make_task"](parent_id=parent.id)
    await branch_setup["db"].flush()
    root = await get_root_task_id(child.id, branch_setup["svc"])
    assert root == parent.id


@pytest.mark.asyncio
async def test_get_root_task_id_unknown_raises(branch_setup: dict) -> None:
    with pytest.raises(BranchNameError, match="Task not found"):
        await get_root_task_id(uuid4(), branch_setup["svc"])
