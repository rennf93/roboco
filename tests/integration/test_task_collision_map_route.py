"""GET /api/tasks/{id}/collision-map — the reviewer/PM collision map route.

Read-only feed for the panel's Collision tab: the task's own declared
surface plus the surfaced siblings (same parent) that would collide with
it. Mirrors test_task_findings_route.py's fixture shape.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.tasks import router as tasks_router
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import TaskNature, TaskStatus, TaskType
from roboco.models.permissions import AgentContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def collision_client(db_session: AsyncSession) -> AsyncIterator[dict]:
    pm = AgentTable(
        id=uuid4(),
        name="PM",
        slug=f"pm-{uuid4().hex[:8]}",
        role=AgentRole.MAIN_PM,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="pm",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(pm)
    await db_session.flush()
    project = ProjectTable(
        id=uuid4(),
        name="CM-Proj",
        slug=f"cm-proj-{uuid4().hex[:6]}",
        git_url="https://example.com/cm.git",
        assigned_cell=Team.BACKEND,
        created_by=pm.id,
    )
    db_session.add(project)
    await db_session.flush()

    app = FastAPI()
    app.include_router(tasks_router, prefix="/api/tasks")

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=cast("UUID", pm.id), role=AgentRole.MAIN_PM, team=None
        )

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "agent": pm, "project": project, "db": db_session}
    app.dependency_overrides.clear()


def _task(setup: dict, **kw: Any) -> TaskTable:
    task = TaskTable(
        id=uuid4(),
        title=kw.pop("title", "t"),
        description=kw.pop("description", "d"),
        acceptance_criteria=["ac"],
        status=kw.pop("status", TaskStatus.IN_PROGRESS),
        priority=kw.pop("priority", 2),
        sequence=kw.pop("sequence", 0),
        task_type=TaskType.CODE,
        nature=TaskNature.TECHNICAL,
        project_id=setup["project"].id,
        created_by=setup["agent"].id,
        team=Team.BACKEND,
        **kw,
    )
    setup["db"].add(task)
    return task


_HDR = {"X-Agent-ID": "ignored", "X-Agent-Role": "main_pm"}


@pytest.mark.asyncio
async def test_collision_map_404_for_missing_task(collision_client: dict) -> None:
    client = collision_client["client"]
    response = await client.get(f"/api/tasks/{uuid4()}/collision-map", headers=_HDR)
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_collision_map_empty_for_rootless_task(collision_client: dict) -> None:
    client = collision_client["client"]
    task = _task(collision_client, intends_to_touch=["roboco/services/git.py"])
    await collision_client["db"].flush()
    response = await client.get(f"/api/tasks/{task.id}/collision-map", headers=_HDR)
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["parent_task_id"] is None
    assert body["intends_to_touch"] == ["roboco/services/git.py"]
    assert body["siblings"] == []


@pytest.mark.asyncio
async def test_collision_map_shows_overlapping_sibling(collision_client: dict) -> None:
    client = collision_client["client"]
    parent = _task(collision_client, title="parent", status=TaskStatus.PENDING)
    await collision_client["db"].flush()
    under = _task(
        collision_client,
        parent_task_id=parent.id,
        title="under review",
        intends_to_touch=["roboco/services/git.py"],
        sequence=0,
    )
    _task(
        collision_client,
        parent_task_id=parent.id,
        title="colliding sibling",
        intends_to_touch=["roboco/services/git.py", "roboco/services/x.py"],
        sequence=1,
    )
    await collision_client["db"].flush()

    response = await client.get(f"/api/tasks/{under.id}/collision-map", headers=_HDR)
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["parent_task_id"] == str(parent.id)
    assert len(body["siblings"]) == 1
    sib_entry = body["siblings"][0]
    assert sib_entry["title"] == "colliding sibling"
    assert "roboco/services/git.py" in sib_entry["overlap"]
    # panel path carries no actual files → drift is empty (the QA/gate
    # evidence envelopes populate it; the panel schema defaults to []).
    assert sib_entry["undeclared"] == []


@pytest.mark.asyncio
async def test_collision_map_omits_non_overlapping_sibling(
    collision_client: dict,
) -> None:
    client = collision_client["client"]
    parent = _task(collision_client, title="parent", status=TaskStatus.PENDING)
    await collision_client["db"].flush()
    under = _task(
        collision_client,
        parent_task_id=parent.id,
        title="under review",
        intends_to_touch=["roboco/services/git.py"],
    )
    _task(
        collision_client,
        parent_task_id=parent.id,
        title="parallel sibling",
        intends_to_touch=["roboco/services/other.py"],
    )
    await collision_client["db"].flush()

    response = await client.get(f"/api/tasks/{under.id}/collision-map", headers=_HDR)
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    assert body["siblings"] == []  # no overlap, no shared migration
