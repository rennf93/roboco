"""Conventions API routes: GET map+health, PUT commit-back, POST restore."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

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

_HDR = {"X-Agent-ID": str(uuid4()), "X-Agent-Role": "main_pm"}


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncIterator[AsyncClient]:
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

    async def _override_db() -> AsyncIterator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=cast("UUID", agent.id), role=AgentRole.MAIN_PM, team=None
        )

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


async def _make_project(client: AsyncClient) -> str:
    resp = await client.post(
        "/api/projects",
        headers=_HDR,
        json={
            "name": f"Project {uuid4().hex[:6]}",
            "slug": f"proj-{uuid4().hex[:6]}",
            "git_url": "https://github.com/example/foo.git",
            "default_branch": "master",
            "assigned_cell": "backend",
        },
    )
    assert resp.status_code == HTTPStatus.CREATED
    return str(resp.json()["id"])


async def test_get_conventions_returns_map_and_health(client: AsyncClient) -> None:
    project_id = await _make_project(client)
    resp = await client.get(f"/api/projects/{project_id}/conventions", headers=_HDR)
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert body["standard"]["rules"]["no_models_in_routers"]["level"] == "block"
    assert body["health"]["status"] in {"missing", "unknown", "ok", "degraded"}


async def test_put_conventions_commits_back(client: AsyncClient) -> None:
    project_id = await _make_project(client)
    resp = await client.put(
        f"/api/projects/{project_id}/conventions",
        headers=_HDR,
        json={
            "version": 1,
            "languages": ["python"],
            "modules": [
                {"path": "app/models", "purpose": "models", "forbidden": ["route"]}
            ],
            "rules": {"no_inline_comments": {"level": "warn"}},
            "custom": [],
            "waivers": [],
        },
    )
    assert resp.status_code == HTTPStatus.OK
    # No workspace on the test project → PR not opened, but the call succeeds.
    assert resp.json()["created"] is False


async def test_restore_conventions(client: AsyncClient) -> None:
    project_id = await _make_project(client)
    resp = await client.post(
        f"/api/projects/{project_id}/conventions/restore", headers=_HDR
    )
    assert resp.status_code == HTTPStatus.OK
    assert "branch" in resp.json()


async def test_get_conventions_unknown_project_404(client: AsyncClient) -> None:
    resp = await client.get(f"/api/projects/{uuid4()}/conventions", headers=_HDR)
    assert resp.status_code == HTTPStatus.NOT_FOUND
