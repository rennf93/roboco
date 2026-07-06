"""Orchestrator control routes (/api/orchestrator/*) are gated to the
CEO/operator identity: the presented ``X-Agent-ID`` is bound to a verified
HMAC token (DB-free panel-token guard) and the role asserted as CEO. In dev
(header-trust) mode a missing token is a no-op; a presented-but-forged token
is still rejected.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.agents_config import issue_agent_token
from roboco.api import deps as _deps
from roboco.api.auth.backend import SESSION_COOKIE_NAME
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

    monkeypatch.setattr(_deps.settings, "cloud_auth_enabled", False)
    client, orch = orch_client
    r = await client.post(
        f"/api/orchestrator/agents/{_AGENT_ID}/spawn",
        headers={"X-Agent-ID": _AGENT_ID, "X-Agent-Role": "ceo"},
    )
    assert r.status_code == _HTTP_201
    orch.spawn_agent.assert_awaited_once()


# ---------------------------------------------------------------------------
# cloud_auth on: cookie dual-path (panel reaches /api/orchestrator/* via cookie)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cloud_auth_forged_ceo_header_no_token_no_cookie_rejected(
    orch_client: tuple[AsyncClient, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """cloud_auth on: bare X-Agent-Role: ceo with no token/cookie is a spoof."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_REQUIRED", raising=False)

    monkeypatch.setattr(_deps.settings, "cloud_auth_enabled", True)
    client, orch = orch_client
    r = await client.post(
        f"/api/orchestrator/agents/{_AGENT_ID}/spawn",
        headers={"X-Agent-ID": _AGENT_ID, "X-Agent-Role": "ceo"},
    )
    assert r.status_code == _HTTP_401
    orch.spawn_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_cloud_auth_valid_ceo_token_passes(
    orch_client: tuple[AsyncClient, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_REQUIRED", raising=False)

    monkeypatch.setattr(_deps.settings, "cloud_auth_enabled", True)
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
async def test_cloud_auth_valid_session_cookie_passes(
    orch_client: tuple[AsyncClient, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Panel path: a valid CEO session cookie reaches the orchestrator."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_REQUIRED", raising=False)

    monkeypatch.setattr(_deps.settings, "cloud_auth_enabled", True)
    client, orch = orch_client
    fake_user = MagicMock()
    with patch(
        "roboco.api.routes.orchestrator.resolve_session_user",
        new=AsyncMock(return_value=fake_user),
    ):
        r = await client.post(
            f"/api/orchestrator/agents/{_AGENT_ID}/spawn",
            headers={
                "X-Agent-ID": _AGENT_ID,
                "X-Agent-Role": "ceo",
            },
            cookies={SESSION_COOKIE_NAME: "valid-session-cookie"},
        )
    assert r.status_code == _HTTP_201
    orch.spawn_agent.assert_awaited_once()


@pytest.mark.asyncio
async def test_cloud_auth_invalid_session_cookie_rejected(
    orch_client: tuple[AsyncClient, MagicMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_REQUIRED", raising=False)

    monkeypatch.setattr(_deps.settings, "cloud_auth_enabled", True)
    client, orch = orch_client
    with patch(
        "roboco.api.routes.orchestrator.resolve_session_user",
        new=AsyncMock(return_value=None),
    ):
        r = await client.post(
            f"/api/orchestrator/agents/{_AGENT_ID}/spawn",
            headers={
                "X-Agent-ID": _AGENT_ID,
                "X-Agent-Role": "ceo",
            },
            cookies={SESSION_COOKIE_NAME: "bogus"},
        )
    assert r.status_code == _HTTP_401
    orch.spawn_agent.assert_not_awaited()
