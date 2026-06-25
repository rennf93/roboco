"""CI_WATCH_SOURCE + list_open_ci_watch_tasks — the dedupe / open-cap basis.

Open ci_watch tasks count toward the cap and block a duplicate; terminal ones
and other-source tasks do not. The git_url scoping keys dedupe on the repo (a
monorepo registers several cell-projects on one git_url), so a watched repo
gets at most one open fix task even across its cell-projects.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from roboco.db.tables import AgentTable, ProjectTable
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
from roboco.models.task import TaskCreateRequest
from roboco.services.task import CI_WATCH_SOURCE, get_task_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
MAIN_PM_UUID = _foundation.AGENTS["main-pm"].uuid
_TWO = 2


async def _get_or_create_agent(
    db: AsyncSession, agent_id: object, role: AgentRole, slug: str
) -> None:
    if await db.get(AgentTable, agent_id) is None:
        db.add(
            AgentTable(
                id=agent_id,
                name=slug,
                slug=f"{slug}-{uuid4().hex[:8]}",
                role=role,
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


async def _seed_project(db: AsyncSession, git_url: str) -> ProjectTable:
    project = ProjectTable(
        id=uuid4(),
        name="P",
        slug=f"p-{uuid4().hex[:8]}",
        git_url=git_url,
        assigned_cell=Team.BACKEND,
        created_by=SYSTEM_UUID,
    )
    db.add(project)
    await db.flush()
    return project


async def _make_ci_watch_task(
    db: AsyncSession,
    project: ProjectTable,
    *,
    source: str = CI_WATCH_SOURCE,
    terminal: bool = False,
) -> None:
    svc = get_task_service(db)
    task = await svc.create(
        TaskCreateRequest(
            title="CI-watch fix",
            description="Fix the CI regression on this project's default branch.",
            acceptance_criteria=["CI is green again"],
            team=Team.MAIN_PM,
            assigned_to=MAIN_PM_UUID,
            created_by=SYSTEM_UUID,
            task_type=TaskType.CODE,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.MEDIUM,
            project_id=project.id,
            status=TaskStatus.PENDING,
            source=source,
            confirmed_by_human=True,
        )
    )
    if terminal:
        task.status = TaskStatus.COMPLETED
        await db.flush()


@pytest.fixture(autouse=True)
async def _agents(db_session: AsyncSession) -> None:
    await _get_or_create_agent(db_session, SYSTEM_UUID, AgentRole.SYSTEM, "system")
    await _get_or_create_agent(db_session, MAIN_PM_UUID, AgentRole.MAIN_PM, "main-pm")


@pytest.mark.asyncio
async def test_lists_only_open_ci_watch_tasks(db_session: AsyncSession) -> None:
    proj = await _seed_project(db_session, "https://github.com/x/a.git")
    await _make_ci_watch_task(db_session, proj)  # open ci_watch
    await _make_ci_watch_task(db_session, proj, terminal=True)  # terminal ci_watch
    await _make_ci_watch_task(db_session, proj, source="manual")  # other source

    open_tasks = await get_task_service(db_session).list_open_ci_watch_tasks()

    assert len(open_tasks) == 1
    assert open_tasks[0].source == CI_WATCH_SOURCE
    assert open_tasks[0].status != TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_git_url_scoping_returns_only_that_repo(
    db_session: AsyncSession,
) -> None:
    proj_a = await _seed_project(db_session, "https://github.com/x/a.git")
    proj_b = await _seed_project(db_session, "https://github.com/x/b.git")
    await _make_ci_watch_task(db_session, proj_a)
    await _make_ci_watch_task(db_session, proj_b)

    svc = get_task_service(db_session)
    assert len(await svc.list_open_ci_watch_tasks()) == _TWO
    scoped = await svc.list_open_ci_watch_tasks(git_url="https://github.com/x/a.git")
    assert len(scoped) == 1
    assert scoped[0].project_id == proj_a.id
