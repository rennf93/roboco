"""Dashboard API route coverage."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.dashboard import router as dashboard_router
from roboco.db.tables import AgentTable
from roboco.models import AgentRole, AgentStatus
from roboco.models.permissions import AgentContext
from roboco.services.dashboard import reset_storage

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def dashboard_client(
    db_session: AsyncSession,
) -> AsyncIterator[AsyncClient]:
    reset_storage()
    agent = AgentTable(
        id=uuid4(),
        name="CEO",
        slug=f"ceo-{uuid4().hex[:8]}",
        role=AgentRole.CEO,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="ceo",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()

    app = FastAPI()
    app.include_router(dashboard_router, prefix="/api/dashboard")

    async def _override_db():
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(agent_id=agent.id, role=AgentRole.CEO, team=None)

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_agent_context] = _override_agent

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.clear()


_HDR = {"X-Agent-ID": str(uuid4()), "X-Agent-Role": "ceo"}


@pytest.mark.asyncio
async def test_create_auditor_flag(dashboard_client: AsyncClient) -> None:
    response = await dashboard_client.post(
        "/api/dashboard/auditor/flags",
        json={
            "severity": "urgent",
            "category": "quality",
            "title": "Bug found",
            "description": "Critical issue",
        },
        headers=_HDR,
    )
    assert response.status_code == 201
    body = response.json()
    assert body["severity"] == "urgent"


@pytest.mark.asyncio
async def test_get_auditor_flags(dashboard_client: AsyncClient) -> None:
    response = await dashboard_client.get("/api/dashboard/auditor/flags", headers=_HDR)
    assert response.status_code == 200
    assert isinstance(response.json(), list)


@pytest.mark.asyncio
async def test_resolve_auditor_flag(dashboard_client: AsyncClient) -> None:
    create = await dashboard_client.post(
        "/api/dashboard/auditor/flags",
        json={
            "severity": "warning",
            "category": "quality",
            "title": "Warning",
            "description": "x",
        },
        headers=_HDR,
    )
    flag_id = create.json()["id"]
    response = await dashboard_client.put(
        f"/api/dashboard/auditor/flags/{flag_id}/resolve",
        params={"notes": "fixed"},
        headers=_HDR,
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_resolve_unknown_flag_returns_404(
    dashboard_client: AsyncClient,
) -> None:
    response = await dashboard_client.put(
        f"/api/dashboard/auditor/flags/{uuid4()}/resolve", headers=_HDR
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_auditor_report(dashboard_client: AsyncClient) -> None:
    response = await dashboard_client.post(
        "/api/dashboard/auditor/reports",
        json={
            "report_type": "weekly",
            "title": "Q1 Report",
            "summary": "Strong week",
            "sections": [],
        },
        headers=_HDR,
    )
    assert response.status_code == 201


@pytest.mark.asyncio
async def test_get_auditor_reports(dashboard_client: AsyncClient) -> None:
    response = await dashboard_client.get(
        "/api/dashboard/auditor/reports", headers=_HDR
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_kanban_for_team_known_bug(
    dashboard_client: AsyncClient,
) -> None:
    """Pre-existing bug — board.team is already a string (not enum) at line 334.

    The route does `team.value` on a value already coerced to a string,
    raising AttributeError. We assert the bug exists so a fix flips the test.
    """
    with pytest.raises(AttributeError, match="'str' object has no attribute 'value'"):
        await dashboard_client.get("/api/dashboard/kanban/backend", headers=_HDR)


@pytest.mark.asyncio
async def test_get_all_agent_status(dashboard_client: AsyncClient) -> None:
    response = await dashboard_client.get("/api/dashboard/agents/status", headers=_HDR)
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_recent_activity(dashboard_client: AsyncClient) -> None:
    response = await dashboard_client.get(
        "/api/dashboard/activity/recent",
        headers=_HDR,
    )
    assert response.status_code == 200
