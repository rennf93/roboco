"""POST /api/a2a/message/send and /message/stream enforce the same HMAC
agent-token gate as the /api/v1/do/* router (``require_any_authenticated_agent``,
token-only, DB-free, no role assertion — the a2a router serves every role).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from roboco.agents_config import issue_agent_token
from roboco.api.deps import get_agent_context
from roboco.api.routes.a2a import router as a2a_router
from roboco.models import AgentRole, Team
from roboco.models.permissions import AgentContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_SECRET = "test-secret-for-a2a-auth"
_AGENT_ID = "00000000-0000-0000-0000-000000000002"
_HTTP_200 = 200
_HTTP_400 = 400
_HTTP_401 = 401


def _message_body() -> dict:
    """A minimal valid SendMessageRequest body.

    ``message.task_id`` defaults to None, so the send route raises
    TASK_ID_REQUIRED (400) AFTER the gate passes — proving the gate let the
    request through without touching the DB. The stream route takes the
    ``else`` (new-task) branch and returns 200 with no DB access.
    """
    return {"message": {"role": "user", "parts": [{"type": "text", "text": "x"}]}}


@pytest.fixture
async def a2a_client() -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(a2a_router, prefix="/api/a2a")

    # /message/send resolves the authenticated caller's slug via
    # get_agent_context (a DB lookup) to use as the responder (#116). This is a
    # DB-free unit test of the token GATE + the route body, so stub the agent
    # context — the gate (require_any_authenticated_agent) still runs real and
    # 401s on a missing/forged token before this dependency resolves.
    async def _stub_agent_context() -> AgentContext:
        return AgentContext(
            agent_id=UUID(_AGENT_ID),
            role=AgentRole.DEVELOPER,
            team=Team.BACKEND,
            slug="dev",
        )

    app.dependency_overrides[get_agent_context] = _stub_agent_context
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# /message/send
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_send_rejects_missing_token_when_required(
    a2a_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Strict mode + no X-Agent-Token => 401, never reaches the handler."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", "true")
    r = await a2a_client.post(
        "/api/a2a/message/send",
        json=_message_body(),
        headers={"X-Agent-ID": _AGENT_ID, "X-Agent-Role": "developer"},
    )
    assert r.status_code == _HTTP_401


@pytest.mark.asyncio
async def test_send_rejects_forged_token_even_in_dev(
    a2a_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A presented-but-forged token is rejected even in header-trust mode."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_REQUIRED", raising=False)
    r = await a2a_client.post(
        "/api/a2a/message/send",
        json=_message_body(),
        headers={
            "X-Agent-ID": _AGENT_ID,
            "X-Agent-Role": "developer",
            "X-Agent-Token": "forged-not-a-real-hmac",
        },
    )
    assert r.status_code == _HTTP_401


@pytest.mark.asyncio
async def test_send_accepts_valid_token(
    a2a_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid token passes the gate; the route body then raises
    TASK_ID_REQUIRED (400) because message.task_id is None — proving the
    gate let the request through (401 would mean the gate rejected it)."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", "true")
    token = issue_agent_token(_AGENT_ID, "developer")
    r = await a2a_client.post(
        "/api/a2a/message/send",
        json=_message_body(),
        headers={
            "X-Agent-ID": _AGENT_ID,
            "X-Agent-Role": "developer",
            "X-Agent-Token": token,
        },
    )
    assert r.status_code == _HTTP_400  # TASK_ID_REQUIRED — gate passed


@pytest.mark.asyncio
async def test_send_dev_mode_missing_token_still_succeeds_gate(
    a2a_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dev mode + no token => no-op, route body runs (400 TASK_ID_REQUIRED).
    Preserves the agent/panel flow in dev exactly as F003/F004 did."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_REQUIRED", raising=False)
    r = await a2a_client.post(
        "/api/a2a/message/send",
        json=_message_body(),
        headers={"X-Agent-ID": _AGENT_ID, "X-Agent-Role": "developer"},
    )
    assert r.status_code == _HTTP_400  # gate passed; route raised TASK_ID_REQUIRED


# ---------------------------------------------------------------------------
# /message/stream
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stream_rejects_missing_token_when_required(
    a2a_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Strict mode + no X-Agent-Token => 401 on the stream route too."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", "true")
    r = await a2a_client.post(
        "/api/a2a/message/stream",
        json=_message_body(),
        headers={"X-Agent-ID": _AGENT_ID, "X-Agent-Role": "developer"},
    )
    assert r.status_code == _HTTP_401


@pytest.mark.asyncio
async def test_stream_rejects_forged_token_even_in_dev(
    a2a_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A presented-but-forged token is rejected even in header-trust mode."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_REQUIRED", raising=False)
    r = await a2a_client.post(
        "/api/a2a/message/stream",
        json=_message_body(),
        headers={
            "X-Agent-ID": _AGENT_ID,
            "X-Agent-Role": "developer",
            "X-Agent-Token": "forged-not-a-real-hmac",
        },
    )
    assert r.status_code == _HTTP_401


@pytest.mark.asyncio
async def test_stream_accepts_valid_token(
    a2a_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid token passes the gate; the stream route returns 200 (SSE) on
    the new-task branch (message.task_id is None -> no DB access)."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", "true")
    token = issue_agent_token(_AGENT_ID, "developer")
    r = await a2a_client.post(
        "/api/a2a/message/stream",
        json=_message_body(),
        headers={
            "X-Agent-ID": _AGENT_ID,
            "X-Agent-Role": "developer",
            "X-Agent-Token": token,
        },
    )
    assert r.status_code == _HTTP_200
