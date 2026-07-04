"""Server-side idle timeout reaps half-open WS sockets.

Each handler wraps ``receive_text()`` in
``asyncio.wait_for(..., timeout=IDLE_TIMEOUT_SECONDS)``; on timeout the
handler's ``finally`` disconnects the idle socket. ``IDLE_TIMEOUT_SECONDS``
is a named module constant (ruff PLR2004). Tests patch it to a tiny value
and use a never-resolved ``Future`` so assertions hold in well under a
second, never relying on real wall-clock timing.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import WebSocketDisconnect
from roboco.api.websocket import (
    IDLE_TIMEOUT_SECONDS,
    ConnectionManager,
    agent_stream,
    notification_stream,
    system_stream,
)


def _mock_ws_for_receive(receive_side_effect: object) -> MagicMock:
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()
    ws.send_json = AsyncMock()
    ws.send_text = AsyncMock()

    if isinstance(receive_side_effect, asyncio.Future):
        # A never-resolving Future means "hang forever" (half-open socket).
        # AsyncMock treats a non-callable side_effect as an iterable, which a
        # Future isn't — so install a real async receive_text that awaits it.
        hang_future = receive_side_effect

        async def _hang_forever() -> str:
            await hang_future  # never resolves; wait_for cancels it on timeout.
            return ""

        ws.receive_text = _hang_forever
    else:
        ws.receive_text = AsyncMock(side_effect=receive_side_effect)
    ws.headers = {}
    ws.query_params = {}
    return ws


# ---------------------------------------------------------------------------
# Constant shape
# ---------------------------------------------------------------------------


def test_idle_timeout_seconds_is_a_named_module_constant() -> None:
    """IDLE_TIMEOUT_SECONDS must be a module-level constant (ruff PLR2004)."""
    assert isinstance(IDLE_TIMEOUT_SECONDS, int | float)
    assert IDLE_TIMEOUT_SECONDS > 0


# ---------------------------------------------------------------------------
# Half-open socket is reaped after the idle timeout (deterministic)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_system_stream_reaps_silent_socket_after_idle_timeout() -> None:
    """A silent half-open socket (receive_text never returns) is reaped after
    the idle timeout — the wait_for raises TimeoutError and the finally
    disconnects. Deterministic: tiny patched timeout + never-resolving Future."""
    mgr = ConnectionManager()
    hang_future: asyncio.Future[str] = asyncio.Future()
    ws = _mock_ws_for_receive(hang_future)
    await mgr.connect_system(ws)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("roboco.api.websocket.manager", mgr)
        mp.setattr("roboco.api.websocket.IDLE_TIMEOUT_SECONDS", 0.05)
        # Must return promptly (well under 2s), not block for the real default.
        await asyncio.wait_for(system_stream(ws), timeout=2.0)

    assert ws not in mgr.system_connections


@pytest.mark.asyncio
async def test_notification_stream_reaps_silent_socket_after_idle_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    agent_id = uuid4()
    mgr = ConnectionManager()
    hang_future: asyncio.Future[str] = asyncio.Future()
    ws = _mock_ws_for_receive(hang_future)
    monkeypatch.setattr(
        "roboco.api.websocket.validate_agent_exists", AsyncMock(return_value=True)
    )
    monkeypatch.setattr("roboco.api.websocket.manager", mgr)
    monkeypatch.setattr("roboco.api.websocket.IDLE_TIMEOUT_SECONDS", 0.05)

    await asyncio.wait_for(notification_stream(ws, agent_id), timeout=2.0)

    assert ws not in mgr.notification_connections.get(agent_id, set())


@pytest.mark.asyncio
async def test_agent_stream_reaps_silent_socket_after_idle_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    target_id = uuid4()
    viewer_id = uuid4()
    mgr = ConnectionManager()
    hang_future: asyncio.Future[str] = asyncio.Future()
    ws = _mock_ws_for_receive(hang_future)
    ws.query_params = {"viewer_id": str(viewer_id)}
    monkeypatch.setattr(
        "roboco.api.websocket.validate_agent_exists", AsyncMock(return_value=True)
    )
    monkeypatch.setattr("roboco.api.websocket.manager", mgr)
    monkeypatch.setattr("roboco.api.websocket.IDLE_TIMEOUT_SECONDS", 0.05)

    await asyncio.wait_for(agent_stream(ws, target_id), timeout=2.0)

    assert ws not in mgr.agent_connections.get(target_id, set())


# ---------------------------------------------------------------------------
# Regression: ping/pong within the idle window keeps the socket alive
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ping_within_idle_window_does_not_disconnect() -> None:
    """A client that sends ping before the idle timeout elapses is NOT
    disconnected — the wait_for resets on each successful receive_text."""
    mgr = ConnectionManager()
    ws = _mock_ws_for_receive(["ping", WebSocketDisconnect()])
    await mgr.connect_system(ws)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("roboco.api.websocket.manager", mgr)
        mp.setattr("roboco.api.websocket.IDLE_TIMEOUT_SECONDS", 30)
        await system_stream(ws)

    # Disconnected only because of the WebSocketDisconnect, not the timeout.
    assert ws not in mgr.system_connections
    ws.send_text.assert_awaited_with("pong")
