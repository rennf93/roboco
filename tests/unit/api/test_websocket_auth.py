"""F004: WebSocket streams must enforce the HMAC panel/CEO token gate when
ROBOCO_AGENT_AUTH_REQUIRED=true.

The /ws/* streams are operator-only (the panel is the sole WS client; agents
use MCP verbs, not WS). nginx injects the CEO panel token as X-Agent-Token on
/ws/ upgrades, but the endpoints never read or verified it — so in strict mode
an agent on the Docker network could hit /ws/notifications/{id} directly and
subscribe to another agent's notifications with no auth. The fix binds each
per-agent WS upgrade to the CEO token: require + verify it in strict mode, and
reject a forged token even in dev mode (same contract as the HTTP role gates).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import WebSocketDisconnect, status
from roboco.agents_config import CEO_AGENT_ID, issue_agent_token
from roboco.api.websocket import agent_stream, notification_stream

if TYPE_CHECKING:
    import pytest as _pytest  # noqa: F401

_SECRET = "test-secret-for-ws-auth"


def _mock_ws(headers: dict[str, str] | None, query: dict[str, str] | None) -> MagicMock:
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()
    ws.send_json = AsyncMock()
    ws.send_text = AsyncMock()
    # One pong then disconnect so the receive loop exits after a successful gate.
    ws.receive_text = AsyncMock(side_effect=["ping", WebSocketDisconnect()])
    ws.headers = headers or {}
    ws.query_params = query or {}
    return ws


@pytest.mark.asyncio
async def test_notification_stream_rejects_missing_token_when_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Strict mode + no X-Agent-Token => policy-violation close, never accepted."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", "true")
    agent_id = uuid4()
    ws = _mock_ws(headers={}, query={})
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "roboco.api.websocket.validate_agent_exists",
            AsyncMock(return_value=True),
        )
        await notification_stream(ws, agent_id)
    ws.close.assert_awaited_once()
    assert ws.close.await_args.kwargs["code"] == status.WS_1008_POLICY_VIOLATION
    ws.accept.assert_not_awaited()


@pytest.mark.asyncio
async def test_notification_stream_rejects_forged_token_even_in_dev(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even in dev mode a presented-but-forged token is rejected."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_REQUIRED", raising=False)
    agent_id = uuid4()
    ws = _mock_ws(headers={"x-agent-token": "forged-not-a-real-hmac"}, query={})
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "roboco.api.websocket.validate_agent_exists",
            AsyncMock(return_value=True),
        )
        await notification_stream(ws, agent_id)
    ws.close.assert_awaited_once()
    assert ws.close.await_args.kwargs["code"] == status.WS_1008_POLICY_VIOLATION
    ws.accept.assert_not_awaited()


@pytest.mark.asyncio
async def test_notification_stream_accepts_valid_panel_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid CEO panel token passes the gate and the socket is accepted."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", "true")
    token = issue_agent_token(CEO_AGENT_ID, "ceo", "")
    agent_id = uuid4()
    ws = _mock_ws(headers={"x-agent-token": token}, query={})
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "roboco.api.websocket.validate_agent_exists",
            AsyncMock(return_value=True),
        )
        await notification_stream(ws, agent_id)
    ws.accept.assert_awaited_once()
    ws.close.assert_not_awaited()


@pytest.mark.asyncio
async def test_agent_stream_rejects_missing_token_when_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate is wired into agent_stream too (viewer_id query param path)."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", "true")
    target_id = uuid4()
    viewer_id = uuid4()
    ws = _mock_ws(headers={}, query={"viewer_id": str(viewer_id)})
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "roboco.api.websocket.validate_agent_exists",
            AsyncMock(return_value=True),
        )
        await agent_stream(ws, target_id)
    ws.close.assert_awaited_once()
    assert ws.close.await_args.kwargs["code"] == status.WS_1008_POLICY_VIOLATION
    ws.accept.assert_not_awaited()
