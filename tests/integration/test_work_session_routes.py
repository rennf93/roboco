"""WorkSession API route coverage."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.work_session import router as ws_router
from roboco.db.tables import AgentTable, ProjectTable, TaskTable
from roboco.models import AgentRole, AgentStatus, Team
from roboco.models.base import (
    TaskNature,
    TaskStatus,
    TaskType,
)
from roboco.models.permissions import AgentContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def ws_client(
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
        name="WS-Proj",
        slug=f"ws-proj-{uuid4().hex[:6]}",
        git_url="https://example.com/r.git",
        assigned_cell=Team.BACKEND,
        created_by=agent.id,
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
        created_by=agent.id,
        team=Team.BACKEND,
    )
    db_session.add(task)
    await db_session.flush()

    app = FastAPI()
    app.include_router(ws_router, prefix="/api/work-sessions")

    async def _override_db():
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=agent.id, role=AgentRole.DEVELOPER, team=Team.BACKEND
        )

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {
            "client": client,
            "agent": agent,
            "project": project,
            "task": task,
        }
    app.dependency_overrides.clear()


_HDR = {"X-Agent-ID": str(uuid4()), "X-Agent-Role": "developer"}


@pytest.mark.asyncio
async def test_list_sessions_empty(ws_client: dict) -> None:
    client = ws_client["client"]
    response = await client.get("/api/work-sessions", headers=_HDR)
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_get_session_not_found(ws_client: dict) -> None:
    client = ws_client["client"]
    response = await client.get(f"/api/work-sessions/{uuid4()}", headers=_HDR)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_session(ws_client: dict) -> None:
    client = ws_client["client"]
    response = await client.post(
        "/api/work-sessions",
        json={
            "project_id": str(ws_client["project"].id),
            "task_id": str(ws_client["task"].id),
            "branch_name": "feature/x",
            "base_branch": "main",
            "target_branch": "main",
        },
        headers=_HDR,
    )
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_get_session_by_id(ws_client: dict) -> None:
    client = ws_client["client"]
    create = await client.post(
        "/api/work-sessions",
        json={
            "project_id": str(ws_client["project"].id),
            "task_id": str(ws_client["task"].id),
            "branch_name": "feature/y",
            "base_branch": "main",
            "target_branch": "main",
        },
        headers=_HDR,
    )
    sid = create.json()["id"]
    response = await client.get(f"/api/work-sessions/{sid}", headers=_HDR)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_add_commit(ws_client: dict) -> None:
    client = ws_client["client"]
    create = await client.post(
        "/api/work-sessions",
        json={
            "project_id": str(ws_client["project"].id),
            "task_id": str(ws_client["task"].id),
            "branch_name": "feature/c",
            "base_branch": "main",
            "target_branch": "main",
        },
        headers=_HDR,
    )
    sid = create.json()["id"]
    response = await client.post(
        f"/api/work-sessions/{sid}/commits",
        json={"commit_sha": "abc123def"},
        headers=_HDR,
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_add_commit_session_not_found(ws_client: dict) -> None:
    client = ws_client["client"]
    response = await client.post(
        f"/api/work-sessions/{uuid4()}/commits",
        json={"commit_sha": "abc"},
        headers=_HDR,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_add_files(ws_client: dict) -> None:
    client = ws_client["client"]
    create = await client.post(
        "/api/work-sessions",
        json={
            "project_id": str(ws_client["project"].id),
            "task_id": str(ws_client["task"].id),
            "branch_name": "feature/f",
            "base_branch": "main",
            "target_branch": "main",
        },
        headers=_HDR,
    )
    sid = create.json()["id"]
    response = await client.post(
        f"/api/work-sessions/{sid}/files",
        json={"file_paths": ["a.py", "b.py"]},
        headers=_HDR,
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_create_pr(ws_client: dict) -> None:
    client = ws_client["client"]
    create = await client.post(
        "/api/work-sessions",
        json={
            "project_id": str(ws_client["project"].id),
            "task_id": str(ws_client["task"].id),
            "branch_name": "feature/p",
            "base_branch": "main",
            "target_branch": "main",
        },
        headers=_HDR,
    )
    sid = create.json()["id"]
    response = await client.post(
        f"/api/work-sessions/{sid}/pr",
        json={"pr_number": 42, "pr_url": "https://github.com/x/y/pull/42"},
        headers=_HDR,
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_complete_session(ws_client: dict) -> None:
    client = ws_client["client"]
    create = await client.post(
        "/api/work-sessions",
        json={
            "project_id": str(ws_client["project"].id),
            "task_id": str(ws_client["task"].id),
            "branch_name": "feature/cmpl",
            "base_branch": "main",
            "target_branch": "main",
        },
        headers=_HDR,
    )
    sid = create.json()["id"]
    response = await client.post(f"/api/work-sessions/{sid}/complete", headers=_HDR)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_abandon_session(ws_client: dict) -> None:
    client = ws_client["client"]
    create = await client.post(
        "/api/work-sessions",
        json={
            "project_id": str(ws_client["project"].id),
            "task_id": str(ws_client["task"].id),
            "branch_name": "feature/ab",
            "base_branch": "main",
            "target_branch": "main",
        },
        headers=_HDR,
    )
    sid = create.json()["id"]
    response = await client.post(
        f"/api/work-sessions/{sid}/abandon",
        params={"reason": "scrapped"},
        headers=_HDR,
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_active_for_task_returns_null(ws_client: dict) -> None:
    client = ws_client["client"]
    response = await client.get(
        f"/api/work-sessions/task/{ws_client['task'].id}", headers=_HDR
    )
    # Returns null body (200 with None) when there's no active session.
    assert response.status_code == 200
