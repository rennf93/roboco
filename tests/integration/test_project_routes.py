"""Project API route coverage."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.project import router as project_router
from roboco.db.tables import AgentTable
from roboco.models import AgentRole, AgentStatus
from roboco.models.permissions import AgentContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def project_client(
    db_session: AsyncSession,
) -> AsyncIterator[AsyncClient]:
    agent = AgentTable(
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
    db_session.add(agent)
    await db_session.flush()

    app = FastAPI()
    app.include_router(project_router, prefix="/api/projects")

    async def _override_db():
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(agent_id=agent.id, role=AgentRole.MAIN_PM, team=None)

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


_HDR = {"X-Agent-ID": str(uuid4()), "X-Agent-Role": "main_pm"}


def _payload() -> dict:
    return {
        "name": f"Project {uuid4().hex[:6]}",
        "slug": f"proj-{uuid4().hex[:6]}",
        "git_url": "https://github.com/example/foo.git",
        "default_branch": "main",
        "assigned_cell": "backend",
    }


@pytest.mark.asyncio
async def test_list_projects_empty(project_client: AsyncClient) -> None:
    response = await project_client.get("/api/projects", headers=_HDR)
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_create_project(project_client: AsyncClient) -> None:
    response = await project_client.post("/api/projects", json=_payload(), headers=_HDR)
    assert response.status_code == 201
    body = response.json()
    assert "id" in body
    assert body["name"].startswith("Project")


@pytest.mark.asyncio
async def test_create_duplicate_returns_409(project_client: AsyncClient) -> None:
    payload = _payload()
    response = await project_client.post("/api/projects", json=payload, headers=_HDR)
    assert response.status_code == 201
    response2 = await project_client.post("/api/projects", json=payload, headers=_HDR)
    assert response2.status_code == 409


@pytest.mark.asyncio
async def test_get_project_not_found(project_client: AsyncClient) -> None:
    response = await project_client.get(f"/api/projects/{uuid4()}", headers=_HDR)
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_project_by_id(project_client: AsyncClient) -> None:
    create_resp = await project_client.post(
        "/api/projects", json=_payload(), headers=_HDR
    )
    pid = create_resp.json()["id"]
    response = await project_client.get(f"/api/projects/{pid}", headers=_HDR)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_project_by_slug(project_client: AsyncClient) -> None:
    payload = _payload()
    await project_client.post("/api/projects", json=payload, headers=_HDR)
    response = await project_client.get(
        f"/api/projects/{payload['slug']}", headers=_HDR
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_update_project(project_client: AsyncClient) -> None:
    create = await project_client.post("/api/projects", json=_payload(), headers=_HDR)
    pid = create.json()["id"]
    response = await project_client.patch(
        f"/api/projects/{pid}",
        json={"name": "Renamed"},
        headers=_HDR,
    )
    assert response.status_code == 200
    assert response.json()["name"] == "Renamed"


@pytest.mark.asyncio
async def test_update_project_not_found(project_client: AsyncClient) -> None:
    response = await project_client.patch(
        f"/api/projects/{uuid4()}",
        json={"name": "x"},
        headers=_HDR,
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_list_projects_filter_by_cell(
    project_client: AsyncClient,
) -> None:
    response = await project_client.get("/api/projects?cell=backend", headers=_HDR)
    assert response.status_code == 200
