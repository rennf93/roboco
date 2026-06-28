"""Operator/system WebSocket stream — ConnectionManager + the /ws/system endpoint.

The system stream carries system-wide events (rate limits) to the panel with no
per-agent keying. These tests exercise the manager's system-connection
bookkeeping and the endpoint's connect → ping/pong → disconnect lifecycle
against mock sockets (no real app/lifespan/Redis).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import WebSocketDisconnect
from roboco.api.websocket import ConnectionManager, manager, system_stream


@pytest.mark.asyncio
async def test_connect_system_accepts_and_tracks() -> None:
    mgr = ConnectionManager()
    ws = MagicMock()
    ws.accept = AsyncMock()

    await mgr.connect_system(ws)

    ws.accept.assert_awaited_once()
    assert ws in mgr.system_connections


@pytest.mark.asyncio
async def test_broadcast_system_sends_to_every_connection() -> None:
    mgr = ConnectionManager()
    ws1, ws2 = MagicMock(), MagicMock()
    ws1.send_text = AsyncMock()
    ws2.send_text = AsyncMock()
    # These sockets are placed directly into the subscription set (bypassing
    # connect_system), so they take the F064 legacy fallback path: broadcast
    # schedules a timeout-bounded send task per socket instead of awaiting
    # send_text inline. Yield once so those tasks run before asserting.
    mgr.system_connections = {ws1, ws2}

    await mgr.broadcast_system({"type": "RATE_LIMIT_HIT", "provider": "anthropic"})
    await asyncio.sleep(0)

    ws1.send_text.assert_awaited_once()
    ws2.send_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_broadcast_system_is_noop_when_empty() -> None:
    mgr = ConnectionManager()
    # No subscribers — must not raise.
    await mgr.broadcast_system({"type": "x"})


def test_disconnect_removes_from_system() -> None:
    mgr = ConnectionManager()
    ws = MagicMock()
    mgr.system_connections.add(ws)

    mgr.disconnect(ws)

    assert ws not in mgr.system_connections


@pytest.mark.asyncio
async def test_system_stream_connect_ping_disconnect() -> None:
    """Endpoint sends 'connected', answers ping with pong, cleans up on close."""
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.send_json = AsyncMock()
    ws.send_text = AsyncMock()
    # One ping, then the client disconnects.
    ws.receive_text = AsyncMock(side_effect=["ping", WebSocketDisconnect()])

    await system_stream(ws)

    ws.accept.assert_awaited_once()
    ws.send_json.assert_awaited_once_with({"type": "connected"})
    ws.send_text.assert_awaited_with("pong")
    # Disconnect handler removed the socket from the global manager.
    assert ws not in manager.system_connections
