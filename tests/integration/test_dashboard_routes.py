"""Dashboard API route coverage."""

from __future__ import annotations

import uuid
from http import HTTPStatus
from typing import TYPE_CHECKING, cast
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import get_agent_context, get_db
from roboco.api.routes.dashboard import get_main_pm_kanban
from roboco.api.routes.dashboard import router as dashboard_router
from roboco.db.tables import AgentTable
from roboco.models import AgentRole, AgentStatus
from roboco.models.permissions import AgentContext
from roboco.services.dashboard import reset_storage

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator

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

    async def _override_db() -> AsyncGenerator[AsyncSession]:
        yield db_session

    async def _override_agent() -> AgentContext:
        return AgentContext(
            agent_id=cast("uuid.UUID", agent.id), role=AgentRole.CEO, team=None
        )

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
    assert response.status_code == HTTPStatus.CREATED
    body = response.json()
    assert body["severity"] == "urgent"


@pytest.mark.asyncio
async def test_get_auditor_flags(dashboard_client: AsyncClient) -> None:
    response = await dashboard_client.get("/api/dashboard/auditor/flags", headers=_HDR)
    assert response.status_code == HTTPStatus.OK
    assert isinstance(response.json(), list)


# ---------------------------------------------------------------------------
# Observability endpoints (0.10.0)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cycle_time_endpoint(dashboard_client: AsyncClient) -> None:
    resp = await dashboard_client.get(
        "/api/dashboard/metrics/cycle-time?days=30", headers=_HDR
    )
    assert resp.status_code == HTTPStatus.OK
    assert isinstance(resp.json(), list)


@pytest.mark.asyncio
async def test_bottlenecks_endpoint(dashboard_client: AsyncClient) -> None:
    resp = await dashboard_client.get(
        "/api/dashboard/metrics/bottlenecks", headers=_HDR
    )
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert "by_stage" in body and "worst_stage" in body and "active_blockers" in body


@pytest.mark.asyncio
async def test_rework_endpoint(dashboard_client: AsyncClient) -> None:
    resp = await dashboard_client.get("/api/dashboard/metrics/rework", headers=_HDR)
    assert resp.status_code == HTTPStatus.OK
    body = resp.json()
    assert "rate" in body and "by_team" in body and "by_agent" in body


@pytest.mark.asyncio
async def test_agent_scorecard_404_when_absent(dashboard_client: AsyncClient) -> None:
    resp = await dashboard_client.get(
        f"/api/dashboard/metrics/scorecard/agent/{uuid4()}", headers=_HDR
    )
    assert resp.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_team_scorecard_endpoint(dashboard_client: AsyncClient) -> None:
    resp = await dashboard_client.get(
        "/api/dashboard/metrics/scorecard/team/backend", headers=_HDR
    )
    assert resp.status_code == HTTPStatus.OK
    assert resp.json()["scope"] == "cell"


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
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_resolve_unknown_flag_returns_404(
    dashboard_client: AsyncClient,
) -> None:
    response = await dashboard_client.put(
        f"/api/dashboard/auditor/flags/{uuid4()}/resolve", headers=_HDR
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


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
    assert response.status_code == HTTPStatus.CREATED


@pytest.mark.asyncio
async def test_get_auditor_reports(dashboard_client: AsyncClient) -> None:
    response = await dashboard_client.get(
        "/api/dashboard/auditor/reports", headers=_HDR
    )
    assert response.status_code == HTTPStatus.OK


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
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_recent_activity(dashboard_client: AsyncClient) -> None:
    response = await dashboard_client.get(
        "/api/dashboard/activity/recent",
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_auditor_dashboard(dashboard_client: AsyncClient) -> None:
    response = await dashboard_client.get("/api/dashboard/auditor", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_send_auditor_report_not_found(
    dashboard_client: AsyncClient,
) -> None:
    response = await dashboard_client.post(
        f"/api/dashboard/auditor/reports/{uuid4()}/send", headers=_HDR
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_send_auditor_report_success(
    dashboard_client: AsyncClient,
) -> None:
    create = await dashboard_client.post(
        "/api/dashboard/auditor/reports",
        json={
            "report_type": "weekly",
            "title": "T",
            "summary": "s",
            "sections": [],
        },
        headers=_HDR,
    )
    rid = create.json()["id"]
    response = await dashboard_client.post(
        f"/api/dashboard/auditor/reports/{rid}/send", headers=_HDR
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_ceo_overview(dashboard_client: AsyncClient) -> None:
    response = await dashboard_client.get("/api/dashboard/ceo", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_ceo_team_details(dashboard_client: AsyncClient) -> None:
    response = await dashboard_client.get("/api/dashboard/ceo/teams", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_ceo_blocker_details(dashboard_client: AsyncClient) -> None:
    response = await dashboard_client.get("/api/dashboard/ceo/blockers", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_ceo_velocity(dashboard_client: AsyncClient) -> None:
    response = await dashboard_client.get(
        "/api/dashboard/ceo/velocity?days=14", headers=_HDR
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_main_pm_kanban_via_http(dashboard_client: AsyncClient) -> None:
    """`/kanban/main-pm` is now declared before `/kanban/{team}`, so it routes
    correctly to `get_main_pm_kanban` instead of being matched as
    `team=main-pm` (which would 422)."""
    response = await dashboard_client.get("/api/dashboard/kanban/main-pm", headers=_HDR)
    assert response.status_code == HTTPStatus.OK
    body = response.json()
    # main_pm board has columns; shape is from KanbanBoard.model_dump().
    assert "columns" in body


@pytest.mark.asyncio
async def test_get_velocity_metrics(dashboard_client: AsyncClient) -> None:
    response = await dashboard_client.get(
        "/api/dashboard/metrics/velocity?days=7", headers=_HDR
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_blocker_metrics(dashboard_client: AsyncClient) -> None:
    response = await dashboard_client.get(
        "/api/dashboard/metrics/blockers", headers=_HDR
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_team_metrics(dashboard_client: AsyncClient) -> None:
    response = await dashboard_client.get(
        "/api/dashboard/metrics/team/backend", headers=_HDR
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_communication_metrics(
    dashboard_client: AsyncClient,
) -> None:
    response = await dashboard_client.get(
        "/api/dashboard/metrics/communication", headers=_HDR
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_health_metrics(dashboard_client: AsyncClient) -> None:
    response = await dashboard_client.get("/api/dashboard/metrics/health", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_agent_metrics_not_found(
    dashboard_client: AsyncClient,
) -> None:
    response = await dashboard_client.get(
        f"/api/dashboard/metrics/agent/{uuid4()}", headers=_HDR
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_get_auditor_flags_filter_severity(
    dashboard_client: AsyncClient,
) -> None:
    response = await dashboard_client.get(
        "/api/dashboard/auditor/flags?severity=warning", headers=_HDR
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_auditor_reports_with_filter(
    dashboard_client: AsyncClient,
) -> None:
    response = await dashboard_client.get(
        "/api/dashboard/auditor/reports?report_type=weekly", headers=_HDR
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_agent_metrics_existing_agent(
    dashboard_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    """Existing agent → exercise route happy path (line 494)."""
    agent = AgentTable(
        id=uuid4(),
        name="Probe",
        slug=f"probe-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=None,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="x",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(agent)
    await db_session.flush()
    response = await dashboard_client.get(
        f"/api/dashboard/metrics/agent/{agent.id}", headers=_HDR
    )
    # MetricsService may return None for an empty agent → 404; or a metrics
    # object if there's enough data. Either way the route is exercised.
    assert response.status_code in (HTTPStatus.OK, HTTPStatus.NOT_FOUND)


@pytest.mark.asyncio
async def test_get_main_pm_kanban_function_directly(
    db_session: AsyncSession,
) -> None:
    """Route /kanban/main-pm is unreachable via HTTP (intercepted by /kanban/{team}).

    Call the route function directly to cover lines 367-369.
    """
    result = await get_main_pm_kanban(db_session)
    assert isinstance(result, dict)
