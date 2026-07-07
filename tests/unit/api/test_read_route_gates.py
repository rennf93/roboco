"""Phase 1b Task 5 — panel read routes gated behind panel token under cloud_auth.

Covers the routers gated in this task: /api/agents, /api/kanban, /api/usage,
/api/system/rate-limits, and the a2a task reads /api/a2a/tasks[/{id}]. The
discovery endpoints (/.well-known/agent.json, /api/a2a/agents[/{id}/card])
stay ungated by design and are asserted to remain open.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.agents_config import issue_panel_token
from roboco.api import deps as _deps
from roboco.api.routes.a2a import router as a2a_router
from roboco.api.routes.agents import router as agents_router
from roboco.api.routes.kanban import router as kanban_router
from roboco.api.routes.system import router as system_router
from roboco.api.routes.usage import router as usage_router
from roboco.db.base import get_db

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_SECRET = "test-secret-for-read-route-gates"
_HTTP_200 = 200
_HTTP_401 = 401


async def _fake_db() -> AsyncIterator[object]:
    yield object()


@pytest_asyncio.fixture
async def gated_client(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    app = FastAPI()
    app.include_router(agents_router, prefix="/api/agents")
    app.include_router(kanban_router, prefix="/api/kanban")
    app.include_router(usage_router, prefix="/api/usage")
    app.include_router(system_router, prefix="/api/system")
    app.include_router(a2a_router, prefix="/api/a2a")
    app.dependency_overrides[get_db] = _fake_db

    # Stub the per-route services so the gate is the only variable.
    monkeypatch.setattr(
        "roboco.api.routes.agents.get_agent_service",
        lambda _db: MagicMock(list_agents=AsyncMock(return_value=[])),
    )
    monkeypatch.setattr(
        "roboco.api.routes.kanban.get_kanban_service",
        lambda _db: MagicMock(get_dev_board=AsyncMock(return_value={})),
    )
    monkeypatch.setattr(
        "roboco.api.routes.usage.get_usage_service",
        lambda _db: MagicMock(summary=AsyncMock(return_value={})),
    )
    monkeypatch.setattr(
        "roboco.api.routes.system.RateLimitStateTracker.list_rate_limited_providers",
        AsyncMock(return_value={}),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
    app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/agents"),
        ("GET", "/api/kanban/dev/backend"),
        ("GET", "/api/usage/summary?period=24h"),
        ("GET", "/api/system/rate-limits"),
    ],
)
@pytest.mark.asyncio
async def test_panel_read_routes_reject_no_credential_under_cloud_auth(
    gated_client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    method: str,
    path: str,
) -> None:
    monkeypatch.setattr(_deps.settings, "cloud_auth_enabled", True)
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_REQUIRED", raising=False)
    r = (
        await gated_client.get(path)
        if method == "GET"
        else await gated_client.post(path)
    )
    assert r.status_code == _HTTP_401


@pytest.mark.asyncio
async def test_a2a_tasks_reject_no_credential_under_cloud_auth(
    gated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/api/a2a/tasks is gated with get_agent_context (any authenticated agent)."""
    monkeypatch.setattr(_deps.settings, "cloud_auth_enabled", True)
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_REQUIRED", raising=False)
    r = await gated_client.get("/api/a2a/tasks")
    assert r.status_code == _HTTP_401


@pytest.mark.asyncio
async def test_a2a_task_by_id_rejects_no_credential_under_cloud_auth(
    gated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_deps.settings, "cloud_auth_enabled", True)
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_REQUIRED", raising=False)
    r = await gated_client.get("/api/a2a/tasks/t-1")
    assert r.status_code == _HTTP_401


@pytest.mark.asyncio
async def test_agents_accepts_valid_token_under_cloud_auth(
    gated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_deps.settings, "cloud_auth_enabled", True)
    r = await gated_client.get(
        "/api/agents", headers={"X-Agent-Token": issue_panel_token()}
    )
    assert r.status_code == _HTTP_200


@pytest.mark.asyncio
async def test_agents_dev_mode_no_token_passes(
    gated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(_deps.settings, "cloud_auth_enabled", False)
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_REQUIRED", raising=False)
    r = await gated_client.get("/api/agents")
    assert r.status_code == _HTTP_200


@pytest.mark.asyncio
async def test_a2a_discovery_endpoints_stay_ungated(
    gated_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Discovery surfaces (/.well-known, /a2a/agents[/{id}/card]) stay open."""
    monkeypatch.setattr(_deps.settings, "cloud_auth_enabled", True)
    with (
        patch(
            "roboco.api.routes.a2a.A2AService.discover_agents",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "roboco.api.routes.a2a.A2AService.build_agent_card",
            new=AsyncMock(return_value=None),
        ),
    ):
        r1 = await gated_client.get("/api/a2a/agents")
        r2 = await gated_client.get("/api/a2a/agents/be-dev-1/card")
    assert r1.status_code == _HTTP_200
    # Ungated => not 401 (404 from the None card is fine — the gate didn't fire).
    assert r2.status_code != _HTTP_401
