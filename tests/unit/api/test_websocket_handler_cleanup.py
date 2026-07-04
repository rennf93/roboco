"""WS route handlers must disconnect on ANY exit path, not just
WebSocketDisconnect.

Each handler adds ``finally: manager.disconnect(websocket)``; ``disconnect``
is idempotent (``set.discard`` / ``dict.pop`` with default), so the
clean-disconnect path and the finally both calling it is safe. Tests use
mock sockets (no real app/Redis) and an isolated ``ConnectionManager``
patched in for the module-global ``manager``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import WebSocketDisconnect
from roboco.api.websocket import (
    ConnectionManager,
    agent_stream,
    notification_stream,
    system_stream,
)


def _mock_ws_for_receive(receive_side_effect: object) -> MagicMock:
    """A socket whose receive_text raises/returns per ``receive_side_effect``."""
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()
    ws.send_json = AsyncMock()
    ws.send_text = AsyncMock()
    ws.receive_text = AsyncMock(side_effect=receive_side_effect)
    ws.headers = {}
    ws.query_params = {}
    return ws


# ---------------------------------------------------------------------------
# system_stream (no per-agent keying)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_system_stream_disconnects_on_non_disconnect_exception() -> None:
    """A non-WebSocketDisconnect exception (e.g. anyio closed-resource during
    shutdown) must still remove the socket from the manager — the old code
    only caught WebSocketDisconnect and leaked the dead socket."""
    mgr = ConnectionManager()
    ws = _mock_ws_for_receive(RuntimeError("connection closed during shutdown"))
    await mgr.connect_system(ws)
    assert ws in mgr.system_connections

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("roboco.api.websocket.manager", mgr)
        with pytest.raises(RuntimeError):
            await system_stream(ws)

    assert ws not in mgr.system_connections


@pytest.mark.asyncio
async def test_system_stream_disconnects_on_cancelled_error() -> None:
    """asyncio.CancelledError during shutdown must also disconnect."""
    mgr = ConnectionManager()
    ws = _mock_ws_for_receive(asyncio.CancelledError())
    await mgr.connect_system(ws)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("roboco.api.websocket.manager", mgr)
        with pytest.raises(asyncio.CancelledError):
            await system_stream(ws)

    assert ws not in mgr.system_connections


# ---------------------------------------------------------------------------
# notification_stream (representative per-agent handler)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_notification_stream_disconnects_on_non_disconnect_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    agent_id = uuid4()
    mgr = ConnectionManager()
    ws = _mock_ws_for_receive(RuntimeError("transport reset"))
    monkeypatch.setattr(
        "roboco.api.websocket.validate_agent_exists", AsyncMock(return_value=True)
    )
    monkeypatch.setattr("roboco.api.websocket.manager", mgr)

    with pytest.raises(RuntimeError):
        await notification_stream(ws, agent_id)

    assert ws not in mgr.notification_connections.get(agent_id, set())
    assert ws not in mgr.connection_agents


# ---------------------------------------------------------------------------
# agent handler (same pattern, distinct subscription set)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_stream_disconnects_on_non_disconnect_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    target_id = uuid4()
    viewer_id = uuid4()
    mgr = ConnectionManager()
    ws = _mock_ws_for_receive(RuntimeError("anyio closed"))
    ws.query_params = {"viewer_id": str(viewer_id)}
    monkeypatch.setattr(
        "roboco.api.websocket.validate_agent_exists", AsyncMock(return_value=True)
    )
    monkeypatch.setattr("roboco.api.websocket.manager", mgr)

    with pytest.raises(RuntimeError):
        await agent_stream(ws, target_id)

    assert ws not in mgr.agent_connections.get(target_id, set())


@pytest.mark.asyncio
async def test_system_stream_clean_disconnect_still_works() -> None:
    """Regression: the clean WebSocketDisconnect path still disconnects (the
    new finally must not break the happy path or double-disconnect)."""
    mgr = ConnectionManager()
    ws = _mock_ws_for_receive(["ping", WebSocketDisconnect()])
    await mgr.connect_system(ws)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("roboco.api.websocket.manager", mgr)
        await system_stream(ws)

    assert ws not in mgr.system_connections
    # pong was answered before disconnect.
    ws.send_text.assert_awaited_with("pong")
