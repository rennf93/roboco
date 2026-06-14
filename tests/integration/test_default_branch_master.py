"""Default branch convention resolves to ``master`` (user-repo convention).

Driven by the real Postgres ``db_session`` fixture so the ORM column default
is exercised by the same SQLAlchemy flush path production uses.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import pytest
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.services.git import GitService
from roboco.services.task import TaskService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_creator(db_session: AsyncSession) -> AgentTable:
    agent = AgentTable(
        id=uuid4(),
        name="System",
        slug=f"system-{uuid4().hex[:8]}",
        role=AgentRole.SYSTEM,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="system",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()
    return agent


@pytest.mark.asyncio
async def test_project_default_branch_defaults_to_master(
    db_session: AsyncSession,
) -> None:
    """A project inserted without a ``default_branch`` resolves to ``master``."""
    creator = await _seed_creator(db_session)
    project = ProjectTable(
        id=uuid4(),
        name="No Branch Project",
        slug=f"proj-{uuid4().hex[:8]}",
        git_url="https://github.com/example/no-branch.git",
        assigned_cell=Team.BACKEND,
        created_by=creator.id,
    )
    db_session.add(project)
    await db_session.flush()
    await db_session.refresh(project)

    assert project.default_branch == "master"


@pytest.mark.asyncio
async def test_resolve_parent_branch_falls_back_to_master(
    db_session: AsyncSession,
) -> None:
    """The parent-branch resolver falls back to ``master`` when the project
    default is falsy."""
    svc = TaskService(db_session)
    task = SimpleNamespace(id=uuid4(), parent_task_id=None)
    project = SimpleNamespace(default_branch="")

    branch = await svc._resolve_parent_branch(cast(TaskTable, task), project)

    assert branch == "master"


@pytest.mark.asyncio
async def test_git_project_default_branch_falls_back_to_master(
    db_session: AsyncSession,
) -> None:
    """The git default-branch lookup falls back to ``master`` when the stored
    project default is falsy."""
    creator = await _seed_creator(db_session)
    slug = f"proj-{uuid4().hex[:8]}"
    project = ProjectTable(
        id=uuid4(),
        name="Falsy Branch Project",
        slug=slug,
        git_url="https://github.com/example/falsy-branch.git",
        default_branch="",
        assigned_cell=Team.BACKEND,
        created_by=creator.id,
    )
    db_session.add(project)
    await db_session.flush()

    svc = GitService(db_session)
    branch = await svc._project_default_branch(slug)

    assert branch == "master"
