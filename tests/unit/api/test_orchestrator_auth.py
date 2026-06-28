"""F026: orchestrator control routes (/api/orchestrator/*) must be gated to
the CEO/operator identity.

``spawn_agent`` / ``stop_agent`` / ``resolve_wait`` / ``mark_waiting`` previously
took no auth dependency at all — any client that could reach the API could
spawn, stop, mark-waiting, or resolve-wait any agent. The fix mirrors the
F004 panel-token guard (DB-free): bind the presented ``X-Agent-ID`` to a
verified HMAC token and assert the role is CEO. In dev (header-trust) mode a
missing token is a no-op (the panel/operator flow keeps working), but a
presented-but-forged token is still rejected — same contract as the v1 flow
role guards and the do router (F003).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.agents_config import issue_agent_token
from roboco.api.deps import _ServiceHolder, set_orchestrator
from roboco.api.routes.orchestrator import router as orch_router

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_SECRET = "test-secret-for-orch-auth"
_AGENT_ID = "00000000-0000-0000-0000-000000000001"
_HTTP_201 = 201
_HTTP_204 = 204
_HTTP_401 = 401
_HTTP_403 = 403


def _mock_orchestrator() -> MagicMock:
    orch = MagicMock()
    orch.spawn_agent = AsyncMock(
        return_value=MagicMock(
            agent_id=_AGENT_ID,
            state=MagicMock(value="starting"),
            current_task_id=None,
            error_count=0,
            started_at=None,
            waiting_for=None,
        )
    )
    orch.stop_agent = AsyncMock(return_value=None)
    return orch


@pytest_asyncio.fixture
async def orch_client() -> AsyncIterator[tuple[AsyncClient, MagicMock]]:
    app = FastAPI()
    app.include_router(orch_router, prefix="/api/orchestrator")
    orch = _mock_orchestrator()
    set_orchestrator(orch)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client, orch
    _ServiceHolder.orchestrator = None
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Strict mode: token required
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_rejects_missing_token_when_required(
    orch_client: tuple[AsyncClient, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strict mode + no X-Agent-Token => 401, never reaches the orchestrator."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", "true")
    client, orch = orch_client
    r = await client.post(
        f"/api/orchestrator/agents/{_AGENT_ID}/spawn",
        headers={"X-Agent-ID": _AGENT_ID, "X-Agent-Role": "ceo"},
    )
    assert r.status_code == _HTTP_401
    orch.spawn_agent.assert_not_awaited()


# ---------------------------------------------------------------------------
# Dev mode: forged token rejected, missing token is a no-op
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_rejects_forged_token_even_in_dev(
    orch_client: tuple[AsyncClient, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A presented-but-forged token is rejected even in header-trust mode."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_REQUIRED", raising=False)
    client, orch = orch_client
    r = await client.post(
        f"/api/orchestrator/agents/{_AGENT_ID}/spawn",
        headers={
            "X-Agent-ID": _AGENT_ID,
            "X-Agent-Role": "ceo",
            "X-Agent-Token": "forged-not-a-real-hmac",
        },
    )
    assert r.status_code == _HTTP_401
    orch.spawn_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_spawn_rejects_non_ceo_role(
    orch_client: tuple[AsyncClient, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A developer (even with a validly-issued token) must not spawn/stop agents."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", "true")
    client, orch = orch_client
    dev_id = str(uuid4())
    token = issue_agent_token(dev_id, "developer")
    r = await client.post(
        f"/api/orchestrator/agents/{_AGENT_ID}/spawn",
        headers={
            "X-Agent-ID": dev_id,
            "X-Agent-Role": "developer",
            "X-Agent-Token": token,
        },
    )
    assert r.status_code == _HTTP_403
    orch.spawn_agent.assert_not_awaited()


# ---------------------------------------------------------------------------
# Legitimate CEO caller succeeds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_accepts_valid_ceo_token(
    orch_client: tuple[AsyncClient, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid CEO token passes the gate and reaches the orchestrator."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", "true")
    client, orch = orch_client
    token = issue_agent_token(_AGENT_ID, "ceo")
    r = await client.post(
        f"/api/orchestrator/agents/{_AGENT_ID}/spawn",
        headers={
            "X-Agent-ID": _AGENT_ID,
            "X-Agent-Role": "ceo",
            "X-Agent-Token": token,
        },
    )
    assert r.status_code == _HTTP_201
    orch.spawn_agent.assert_awaited_once()


@pytest.mark.asyncio
async def test_stop_accepts_valid_ceo_token(
    orch_client: tuple[AsyncClient, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate is wired into stop_agent too."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", "true")
    client, orch = orch_client
    token = issue_agent_token(_AGENT_ID, "ceo")
    r = await client.post(
        f"/api/orchestrator/agents/{_AGENT_ID}/stop",
        headers={
            "X-Agent-ID": _AGENT_ID,
            "X-Agent-Role": "ceo",
            "X-Agent-Token": token,
        },
    )
    assert r.status_code == _HTTP_204
    orch.stop_agent.assert_awaited_once()


@pytest.mark.asyncio
async def test_dev_mode_missing_token_still_succeeds(
    orch_client: tuple[AsyncClient, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dev mode (no ROBOCO_AGENT_AUTH_REQUIRED) + no token => no-op, route runs.
    Preserves the panel/operator flow in dev exactly as F003/F004 did."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_REQUIRED", raising=False)
    client, orch = orch_client
    r = await client.post(
        f"/api/orchestrator/agents/{_AGENT_ID}/spawn",
        headers={"X-Agent-ID": _AGENT_ID, "X-Agent-Role": "ceo"},
    )
    assert r.status_code == _HTTP_201
    orch.spawn_agent.assert_awaited_once()
