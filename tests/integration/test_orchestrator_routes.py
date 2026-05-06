"""Orchestrator API route coverage."""

from __future__ import annotations

from datetime import UTC, datetime
from http import HTTPStatus
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.api.deps import _ServiceHolder, set_orchestrator
from roboco.api.routes.orchestrator import router as orch_router

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest_asyncio.fixture
async def orch_client() -> AsyncIterator[tuple[AsyncClient, MagicMock]]:
    app = FastAPI()
    app.include_router(orch_router, prefix="/api/orchestrator")

    orchestrator = MagicMock()
    set_orchestrator(orchestrator)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, orchestrator
    _ServiceHolder.orchestrator = None
    app.dependency_overrides.clear()


_HDR = {"X-Agent-ID": str(uuid4()), "X-Agent-Role": "ceo"}


# ---------------------------------------------------------------------------
# /status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_status(orch_client: tuple[AsyncClient, MagicMock]) -> None:
    client, orch = orch_client
    orch.get_status_summary = MagicMock(
        return_value={
            "total": 1,
            "by_state": {"running": 1},
            "waiting_count": 0,
            "agents": [
                {
                    "agent_id": "be-dev-1",
                    "state": "running",
                    "task_id": None,
                    "error_count": 0,
                    "started_at": datetime.now(UTC).isoformat(),
                }
            ],
        }
    )
    waiting_record = SimpleNamespace(
        agent_id="be-qa-1",
        task_id=None,
        waiting_for="qa",
        waiting_since=datetime.now(UTC),
        context={},
    )
    orch.get_waiting_agents = MagicMock(return_value={"be-dev-1": waiting_record})
    response = await client.get("/api/orchestrator/status", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_status_no_started_at(
    orch_client: tuple[AsyncClient, MagicMock],
) -> None:
    client, orch = orch_client
    orch.get_status_summary = MagicMock(
        return_value={
            "total": 1,
            "by_state": {"running": 1},
            "waiting_count": 0,
            "agents": [
                {
                    "agent_id": "be-dev-1",
                    "state": "running",
                    "task_id": None,
                    "error_count": 0,
                    "started_at": None,
                }
            ],
        }
    )
    orch.get_waiting_agents = MagicMock(return_value={})
    response = await client.get("/api/orchestrator/status", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


# ---------------------------------------------------------------------------
# /agents/{agent_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_agent_status_not_found(
    orch_client: tuple[AsyncClient, MagicMock],
) -> None:
    client, orch = orch_client
    orch.get_instance = MagicMock(return_value=None)
    response = await client.get("/api/orchestrator/agents/be-dev-1", headers=_HDR)
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_get_agent_status_success(
    orch_client: tuple[AsyncClient, MagicMock],
) -> None:
    client, orch = orch_client
    instance = SimpleNamespace(
        agent_id="be-dev-1",
        state=SimpleNamespace(value="running"),
        current_task_id=None,
        error_count=0,
        started_at=datetime.now(UTC),
    )
    orch.get_instance = MagicMock(return_value=instance)
    waiting_record = SimpleNamespace(
        waiting_for="qa",
        agent_id="be-dev-1",
        task_id=None,
        waiting_since=datetime.now(UTC),
        context={},
    )
    orch.get_waiting_agents = MagicMock(return_value={"be-dev-1": waiting_record})
    response = await client.get("/api/orchestrator/agents/be-dev-1", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


@pytest.mark.asyncio
async def test_get_agent_status_not_waiting(
    orch_client: tuple[AsyncClient, MagicMock],
) -> None:
    client, orch = orch_client
    instance = SimpleNamespace(
        agent_id="be-dev-1",
        state=SimpleNamespace(value="running"),
        current_task_id=None,
        error_count=0,
        started_at=datetime.now(UTC),
    )
    orch.get_instance = MagicMock(return_value=instance)
    orch.get_waiting_agents = MagicMock(return_value={})
    response = await client.get("/api/orchestrator/agents/be-dev-1", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


# ---------------------------------------------------------------------------
# /waiting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_waiting_agents(
    orch_client: tuple[AsyncClient, MagicMock],
) -> None:
    client, orch = orch_client
    record = SimpleNamespace(
        agent_id="be-dev-1",
        task_id="t1",
        waiting_for="qa",
        waiting_since=datetime.now(UTC),
        context={},
    )
    orch.get_waiting_agents = MagicMock(return_value={"be-dev-1": record})
    response = await client.get("/api/orchestrator/waiting", headers=_HDR)
    assert response.status_code == HTTPStatus.OK


# ---------------------------------------------------------------------------
# /agents/{agent_id}/spawn
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_agent_not_found(
    orch_client: tuple[AsyncClient, MagicMock],
) -> None:
    client, orch = orch_client
    orch.spawn_agent = AsyncMock(side_effect=FileNotFoundError("missing"))
    response = await client.post(
        "/api/orchestrator/agents/be-dev-1/spawn", headers=_HDR
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_spawn_agent_internal_error(
    orch_client: tuple[AsyncClient, MagicMock],
) -> None:
    client, orch = orch_client
    orch.spawn_agent = AsyncMock(side_effect=RuntimeError("boom"))
    response = await client.post(
        "/api/orchestrator/agents/be-dev-1/spawn",
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.INTERNAL_SERVER_ERROR


@pytest.mark.asyncio
async def test_spawn_agent_success(
    orch_client: tuple[AsyncClient, MagicMock],
) -> None:
    client, orch = orch_client
    instance = SimpleNamespace(
        agent_id="be-dev-1",
        state=SimpleNamespace(value="starting"),
        current_task_id="t1",
        error_count=0,
        started_at=datetime.now(UTC),
    )
    orch.spawn_agent = AsyncMock(return_value=instance)
    response = await client.post(
        "/api/orchestrator/agents/be-dev-1/spawn",
        json={
            "agent_id": "be-dev-1",
            "initial_prompt": "go",
            "task_id": "t1",
            "model": "claude-opus",
        },
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.CREATED


# ---------------------------------------------------------------------------
# /agents/{agent_id}/stop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stop_agent(
    orch_client: tuple[AsyncClient, MagicMock],
) -> None:
    client, orch = orch_client
    orch.stop_agent = AsyncMock(return_value=None)
    response = await client.post("/api/orchestrator/agents/be-dev-1/stop", headers=_HDR)
    assert response.status_code == HTTPStatus.NO_CONTENT


# ---------------------------------------------------------------------------
# /agents/{agent_id}/resolve-wait
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_resolve_wait_not_found(
    orch_client: tuple[AsyncClient, MagicMock],
) -> None:
    client, orch = orch_client
    orch.resolve_wait = AsyncMock(return_value=None)
    response = await client.post(
        "/api/orchestrator/agents/be-dev-1/resolve-wait",
        json={"resolution": {"action": "fixed"}},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.NOT_FOUND


@pytest.mark.asyncio
async def test_resolve_wait_success(
    orch_client: tuple[AsyncClient, MagicMock],
) -> None:
    client, orch = orch_client
    instance = SimpleNamespace(
        agent_id="be-dev-1",
        state=SimpleNamespace(value="running"),
        current_task_id=None,
        error_count=0,
        started_at=datetime.now(UTC),
    )
    orch.resolve_wait = AsyncMock(return_value=instance)
    response = await client.post(
        "/api/orchestrator/agents/be-dev-1/resolve-wait",
        json={"resolution": {"action": "ok"}},
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.OK


# ---------------------------------------------------------------------------
# /agents/{agent_id}/mark-waiting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mark_waiting(
    orch_client: tuple[AsyncClient, MagicMock],
) -> None:
    client, orch = orch_client
    orch.mark_waiting_long = AsyncMock(return_value=None)
    response = await client.post(
        "/api/orchestrator/agents/be-dev-1/mark-waiting?waiting_for=qa&task_id=t1",
        headers=_HDR,
    )
    assert response.status_code == HTTPStatus.NO_CONTENT


@pytest.mark.asyncio
async def test_orchestrator_not_initialized() -> None:
    """When orchestrator isn't set, get_orchestrator raises 503."""
    app = FastAPI()
    app.include_router(orch_router, prefix="/api/orchestrator")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/orchestrator/status", headers=_HDR)
    assert response.status_code == HTTPStatus.SERVICE_UNAVAILABLE
