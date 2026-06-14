"""Agents API route coverage."""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_db
from roboco.api.routes.agents import router as agents_router
from roboco.db.tables import AgentTable
from roboco.models import AgentRole, AgentStatus, Team

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def agents_client(
    db_session: AsyncSession,
) -> AsyncIterator[dict]:
    dev = AgentTable(
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
    db_session.add(dev)
    await db_session.flush()

    app = FastAPI()
    app.include_router(agents_router, prefix="/api/agents")

    async def _override_db() -> AsyncGenerator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = _override_db

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield {"client": client, "agent": dev}
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_agents(agents_client: dict) -> None:
    client = agents_client["client"]
    response = await client.get("/api/agents")
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_list_agents_filter_by_role(agents_client: dict) -> None:
    client = agents_client["client"]
    response = await client.get("/api/agents?role=developer")
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_list_agents_filter_by_team(agents_client: dict) -> None:
    client = agents_client["client"]
    response = await client.get("/api/agents?team=backend")
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_list_agents_invalid_role_returns_400(agents_client: dict) -> None:
    client = agents_client["client"]
    response = await client.get("/api/agents?role=ghost")
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_list_agents_invalid_team_returns_400(agents_client: dict) -> None:
    client = agents_client["client"]
    response = await client.get("/api/agents?team=mars")
    assert response.status_code == HTTPStatus.BAD_REQUEST


@pytest.mark.asyncio
async def test_get_agent_by_uuid(agents_client: dict) -> None:
    client = agents_client["client"]
    response = await client.get(f"/api/agents/{agents_client['agent'].id}")
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_agent_by_slug(agents_client: dict) -> None:
    client = agents_client["client"]
    response = await client.get(f"/api/agents/{agents_client['agent'].slug}")
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_agent_unknown(agents_client: dict) -> None:
    client = agents_client["client"]
    response = await client.get(f"/api/agents/{uuid4()}")
    assert response.status_code == HTTPStatus.NOT_FOUND
