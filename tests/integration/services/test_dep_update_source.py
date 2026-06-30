"""DEP_UPDATE_SOURCE + list_open_dep_update_tasks — the dedupe / open-cap basis.

Open dep_update tasks count toward the cap and block a duplicate; terminal ones
and other-source tasks do not. The git_url scoping keys dedupe on the repo so a
monorepo gets at most one open dependency-update task across its cell-projects.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

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
from roboco.services.task import DEP_UPDATE_SOURCE, get_task_service

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


async def _make_task(
    db: AsyncSession,
    project: ProjectTable,
    *,
    source: str = DEP_UPDATE_SOURCE,
    terminal: bool = False,
) -> None:
    task = await get_task_service(db).create(
        TaskCreateRequest(
            title="Update dependencies",
            description="Upgrade dependencies to latest compatible; gate must pass.",
            acceptance_criteria=["lockfiles refreshed", "gate green"],
            team=Team.MAIN_PM,
            assigned_to=MAIN_PM_UUID,
            created_by=SYSTEM_UUID,
            task_type=TaskType.PLANNING,
            nature=TaskNature.TECHNICAL,
            estimated_complexity=Complexity.MEDIUM,
            project_id=cast("UUID", project.id),
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
async def test_lists_only_open_dep_update_tasks(db_session: AsyncSession) -> None:
    proj = await _seed_project(db_session, "https://github.com/x/a.git")
    await _make_task(db_session, proj)
    await _make_task(db_session, proj, terminal=True)
    await _make_task(db_session, proj, source="manual")

    open_tasks = await get_task_service(db_session).list_open_dep_update_tasks()

    assert len(open_tasks) == 1
    assert open_tasks[0].source == DEP_UPDATE_SOURCE
    assert open_tasks[0].status != TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_git_url_scoping(db_session: AsyncSession) -> None:
    proj_a = await _seed_project(db_session, "https://github.com/x/a.git")
    proj_b = await _seed_project(db_session, "https://github.com/x/b.git")
    await _make_task(db_session, proj_a)
    await _make_task(db_session, proj_b)

    svc = get_task_service(db_session)
    assert len(await svc.list_open_dep_update_tasks()) == _TWO
    scoped = await svc.list_open_dep_update_tasks(git_url="https://github.com/x/a.git")
    assert len(scoped) == 1
    assert scoped[0].project_id == proj_a.id


@pytest.mark.asyncio
async def test_git_url_accidentals_scoping(db_session: AsyncSession) -> None:
    """#1267: a dep_update task open on ``.../a.git`` is found when scoping by a
    git_url that differs only by a ``.git`` suffix / trailing slash — the dedupe
    key is the normalized repo, not the exact string."""
    proj_a = await _seed_project(db_session, "https://github.com/x/a.git")
    await _make_task(db_session, proj_a)

    svc = get_task_service(db_session)
    # Same repo, accidental variants — each scope finds the one open task.
    for variant in ("https://github.com/x/a", "https://github.com/x/a.git/"):
        scoped = await svc.list_open_dep_update_tasks(git_url=variant)
        assert len(scoped) == 1
        assert scoped[0].project_id == proj_a.id
