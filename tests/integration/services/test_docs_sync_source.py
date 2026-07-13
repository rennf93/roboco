"""DOCS_SYNC_SOURCE + list_open_docs_sync_tasks — the dedupe + open-cap basis.

Open docs_sync tasks count toward the cap and block a duplicate per release
version; terminal ones and tasks from other sources do not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

import pytest
from roboco.db.tables import AgentTable, ProjectTable
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
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
from roboco.services.task import DOCS_SYNC_SOURCE, get_task_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
MAIN_PM_UUID = _foundation.AGENTS["main-pm"].uuid
_VERSION = "0.23.0"
_TWO = 2
_ONE = 1


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


async def _seed_project(db: AsyncSession) -> ProjectTable:
    project = ProjectTable(
        id=uuid4(),
        name="RoboCo Website",
        slug=f"website-{uuid4().hex[:8]}",
        git_url="https://github.com/rennf93/roboco-website.git",
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
    source: str = DOCS_SYNC_SOURCE,
    terminal: bool = False,
    version: str | None = None,
) -> None:
    markers_dict: dict[str, object] = {}
    if version is not None:
        markers_dict[markers.DOCS_SYNC_RELEASE_VERSION] = version
    task = await get_task_service(db).create(
        TaskCreateRequest(
            title=f"Update docs for v{version or 'unknown'}",
            description="Refresh published docs to match the shipped release.",
            acceptance_criteria=["docs refreshed", "gate green"],
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
    if markers_dict:
        task.orchestration_markers = markers_dict
    if terminal:
        task.status = TaskStatus.COMPLETED
    await db.flush()


@pytest.fixture(autouse=True)
async def _agents(db_session: AsyncSession) -> None:
    await _get_or_create_agent(db_session, SYSTEM_UUID, AgentRole.SYSTEM, "system")
    await _get_or_create_agent(db_session, MAIN_PM_UUID, AgentRole.MAIN_PM, "main-pm")


@pytest.mark.asyncio
async def test_lists_only_open_docs_sync_tasks(db_session: AsyncSession) -> None:
    proj = await _seed_project(db_session)
    await _make_task(db_session, proj, version=_VERSION)
    await _make_task(db_session, proj, terminal=True, version=_VERSION)
    await _make_task(db_session, proj, source="manual", version=_VERSION)

    open_tasks = await get_task_service(db_session).list_open_docs_sync_tasks()

    assert len(open_tasks) == _ONE
    assert open_tasks[0].source == DOCS_SYNC_SOURCE
    assert open_tasks[0].status != TaskStatus.COMPLETED


@pytest.mark.asyncio
async def test_version_scoping(db_session: AsyncSession) -> None:
    proj = await _seed_project(db_session)
    await _make_task(db_session, proj, version="0.23.0")
    await _make_task(db_session, proj, version="0.24.0")

    svc = get_task_service(db_session)
    assert len(await svc.list_open_docs_sync_tasks()) == _TWO
    scoped = await svc.list_open_docs_sync_tasks(version="0.23.0")
    assert len(scoped) == _ONE
    assert markers.get_docs_sync_release_version(scoped[0]) == "0.23.0"
