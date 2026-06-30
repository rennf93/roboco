"""WebSocket streams (/ws/*, operator-only — the panel is the sole WS client)
enforce the HMAC panel/CEO token gate when ROBOCO_AGENT_AUTH_REQUIRED=true:
each per-agent WS upgrade requires + verifies the CEO token in strict mode
and rejects a forged token even in dev mode (same contract as the HTTP role
gates).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import WebSocketDisconnect, status
from roboco.agents_config import CEO_AGENT_ID, issue_agent_token
from roboco.api.websocket import (
    ConnectionManager,
    agent_stream,
    channel_stream,
    notification_stream,
    system_stream,
)

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


# ---------------------------------------------------------------------------
# The channel stream must be usable by a panel-token holder. It previously
# called validate_channel_access, which HTTP-loopbacked to a non-existent
# /api/permissions/check endpoint — every connection 404'd → False → the stream
# closed with WS_1008_POLICY_VIOLATION for every client (the channel live-stream
# was dead). Post-F004 the panel-token gate IS the channel-stream authorization
# (the CEO panel is the sole WS client and may view every channel), so the
# broken loopback check is removed rather than replaced with theater.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_channel_stream_accepts_panel_token_holder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A panel-token holder supplying an agent_id query param is accepted and
    registered on the channel stream — not fail-closed by a dead permission
    check that 404s against a non-existent endpoint."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", "true")
    token = issue_agent_token(CEO_AGENT_ID, "ceo", "")
    channel_id = uuid4()
    viewer_id = uuid4()
    mgr = ConnectionManager()
    ws = _mock_ws(
        headers={"x-agent-token": token},
        query={"agent_id": str(viewer_id)},
    )
    monkeypatch.setattr("roboco.api.websocket.manager", mgr)

    await channel_stream(ws, channel_id)

    ws.accept.assert_awaited_once()
    # Not fail-closed by a dead permission check.
    ws.close.assert_not_awaited()
    # The "connected" confirmation is sent immediately after connect_channel
    # registers the socket, and its subscriber_count proves the socket was in
    # the channel's subscription set at confirmation time (the mock then raises
    # WebSocketDisconnect so the finally disconnects it — the normal clean
    # exit, not a fail-close).
    confirmation = ws.send_json.await_args.args[0]
    assert confirmation["type"] == "connected"
    assert confirmation["channel_id"] == str(channel_id)
    assert confirmation["subscriber_count"] == 1


@pytest.mark.asyncio
async def test_system_stream_rejects_missing_token_when_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``/ws/system`` was the only /ws/* stream that was ungated (#24). In strict
    mode a missing CEO token must close it with policy-violation, never accept —
    matching every sibling /ws/* handler."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_REQUIRED", "true")
    ws = _mock_ws(headers={}, query={})
    await system_stream(ws)
    ws.close.assert_awaited_once()
    assert ws.close.await_args.kwargs["code"] == status.WS_1008_POLICY_VIOLATION
    ws.accept.assert_not_awaited()


@pytest.mark.asyncio
async def test_system_stream_rejects_forged_token_even_in_dev(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A presented-but-forged token is rejected even in dev mode."""
    monkeypatch.setenv("ROBOCO_AGENT_AUTH_SECRET", _SECRET)
    monkeypatch.delenv("ROBOCO_AGENT_AUTH_REQUIRED", raising=False)
    ws = _mock_ws(headers={"x-agent-token": "forged-not-a-real-hmac"}, query={})
    await system_stream(ws)
    ws.close.assert_awaited_once()
    assert ws.close.await_args.kwargs["code"] == status.WS_1008_POLICY_VIOLATION
    ws.accept.assert_not_awaited()
