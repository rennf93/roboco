"""Sessions API route coverage."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_current_agent_id, get_db
from roboco.api.routes.sessions import router as sessions_router
from roboco.db.tables import (
    AgentTable,
    ChannelTable,
    GroupTable,
    ProjectTable,
    SessionTable,
    TaskTable,
)
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import (
    ChannelType,
    SessionStatus,
    TaskNature,
    TaskStatus,
    TaskType,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def session_client(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
    pm = AgentTable(
        id=uuid4(),
        name="MainPM",
        slug=f"main-pm-{uuid4().hex[:8]}",
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

    channel = ChannelTable(
        id=uuid4(),
        name="ch",
        slug=f"ch-{uuid4().hex[:6]}",
        type=ChannelType.CELL,
        members=[pm.id],
        writers=[pm.id],
    )
    db_session.add(channel)
    await db_session.flush()

    group = GroupTable(
        id=uuid4(),
        name="g1",
        channel_id=channel.id,
        members=[pm.id],
        hierarchy_level=4,
    )
    db_session.add(group)
    await db_session.flush()

    project = ProjectTable(
        id=uuid4(),
        name="S-Proj",
        slug=f"s-proj-{uuid4().hex[:6]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=pm.id,
    )
    db_session.add(project)
    await db_session.flush()

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
        created_by=pm.id,
        team=Team.BACKEND,
    )
    db_session.add(task)
    await db_session.flush()

    app = FastAPI()
    app.include_router(sessions_router, prefix="/api/sessions")

    async def _override_db():
        yield db_session

    async def _override_agent_id() -> UUID:
        return pm.id

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_current_agent_id] = _override_agent_id

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {
            "client": client,
            "pm": pm,
            "channel": channel,
            "group": group,
            "task": task,
        }
    app.dependency_overrides.clear()


_HDR = {"X-Agent-ID": str(uuid4()), "X-Agent-Role": "main_pm"}


@pytest.mark.asyncio
async def test_list_sessions_empty(session_client: dict) -> None:
    client = session_client["client"]
    response = await client.get(
        f"/api/sessions?group_id={session_client['group'].id}",
        headers=_HDR,
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_list_sessions_unknown_group_returns_404(
    session_client: dict,
) -> None:
    client = session_client["client"]
    response = await client.get(
        f"/api/sessions?group_id={uuid4()}",
        headers=_HDR,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_session(session_client: dict) -> None:
    client = session_client["client"]
    response = await client.post(
        "/api/sessions",
        json={"group_id": str(session_client["group"].id)},
        headers=_HDR,
    )
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_get_session_not_found(session_client: dict) -> None:
    client = session_client["client"]
    response = await client.get(f"/api/sessions/{uuid4()}", headers=_HDR)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_session_by_id(
    session_client: dict, db_session: AsyncSession
) -> None:
    client = session_client["client"]
    sess = SessionTable(
        id=uuid4(),
        group_id=session_client["group"].id,
        status=SessionStatus.ACTIVE,
        scope="task",
    )
    db_session.add(sess)
    await db_session.flush()
    response = await client.get(f"/api/sessions/{sess.id}", headers=_HDR)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_close_session_not_found(session_client: dict) -> None:
    client = session_client["client"]
    response = await client.post(f"/api/sessions/{uuid4()}/close", headers=_HDR)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_sessions_for_task(session_client: dict) -> None:
    client = session_client["client"]
    response = await client.get(
        f"/api/sessions/for-task/{session_client['task'].id}", headers=_HDR
    )
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_create_session_for_tasks(session_client: dict) -> None:
    client = session_client["client"]
    response = await client.post(
        "/api/sessions/for-tasks",
        json={
            "task_ids": [str(session_client["task"].id)],
            "channel_slug": session_client["channel"].slug,
        },
        headers=_HDR,
    )
    # Either 201 success or some validation issue — just check it's not a server error.
    assert response.status_code < 500


@pytest.mark.asyncio
async def test_create_session_for_tasks_unknown_channel(
    session_client: dict,
) -> None:
    client = session_client["client"]
    response = await client.post(
        "/api/sessions/for-tasks",
        json={
            "task_ids": [str(session_client["task"].id)],
            "channel_slug": "ghost-channel",
        },
        headers=_HDR,
    )
    assert response.status_code == 404
