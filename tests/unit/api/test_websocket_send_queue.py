"""Per-connection send queue + send timeout — one slow WS client must not
back-pressure ALL event delivery to ALL clients.

Each registered connection gets a bounded send queue + a sender coroutine
that drains it, with ``send_text`` behind
``asyncio.wait_for(..., timeout=SEND_TIMEOUT_SECONDS)``. Broadcasts become
fire-and-enqueue: a slow client's queue fills, then drops/overflows (logged)
instead of blocking the fan-out. Tests patch ``SEND_TIMEOUT_SECONDS`` to a
tiny value and use a never-resolved ``Future`` so assertions hold in well
under a second, never relying on real wall-clock timing.
"""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.api.websocket import (
    MAX_SEND_QUEUE,
    SEND_TIMEOUT_SECONDS,
    ConnectionManager,
)


def _make_ws(*, send_side_effect: object | None = None) -> MagicMock:
    ws = MagicMock()
    ws.accept = AsyncMock()
    ws.close = AsyncMock()
    ws.send_json = AsyncMock()
    if send_side_effect is None:
        ws.send_text = AsyncMock()
    elif isinstance(send_side_effect, asyncio.Future):

        async def _hang(*_args: object) -> None:
            await send_side_effect  # never resolves

        ws.send_text = _hang
    else:
        ws.send_text = AsyncMock(side_effect=send_side_effect)
    return ws


# ---------------------------------------------------------------------------
# Constants shape
# ---------------------------------------------------------------------------


def test_send_constants_are_named_module_constants() -> None:
    """SEND_TIMEOUT_SECONDS + MAX_SEND_QUEUE must be module-level constants."""
    assert isinstance(SEND_TIMEOUT_SECONDS, int | float)
    assert SEND_TIMEOUT_SECONDS > 0
    assert isinstance(MAX_SEND_QUEUE, int)
    assert MAX_SEND_QUEUE > 0


# ---------------------------------------------------------------------------
# Broadcast is fire-and-enqueue: a slow registered client does NOT block it
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_broadcast_returns_promptly_with_slow_registered_client() -> None:
    """A registered connection whose send_text never returns must NOT block
    broadcast — broadcast enqueues (non-blocking) and returns immediately."""
    mgr = ConnectionManager()
    hang: asyncio.Future[None] = asyncio.Future()
    slow_ws = _make_ws(send_side_effect=hang)
    await mgr.connect_system(slow_ws)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("roboco.api.websocket.SEND_TIMEOUT_SECONDS", 0.1)
        # Must return in well under 1s — enqueue must not await the slow send.
        await asyncio.wait_for(
            mgr.broadcast_system({"type": "RATE_LIMIT_HIT"}), timeout=1.0
        )

    # Cleanup: disconnect cancels the stuck sender task.
    mgr.disconnect(slow_ws)


@pytest.mark.asyncio
async def test_slow_client_does_not_block_fast_client() -> None:
    """Two registered connections — one slow (send_text hangs), one fast.
    The fast client receives the message promptly; the slow client's send
    does not delay the fast client's delivery nor the broadcast return."""
    mgr = ConnectionManager()
    hang: asyncio.Future[None] = asyncio.Future()
    slow_ws = _make_ws(send_side_effect=hang)
    fast_ws = _make_ws()  # default AsyncMock send_text returns immediately.
    await mgr.connect_system(slow_ws)
    await mgr.connect_system(fast_ws)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("roboco.api.websocket.SEND_TIMEOUT_SECONDS", 0.1)
        # Broadcast returns promptly despite the slow client.
        await asyncio.wait_for(
            mgr.broadcast_system({"type": "USAGE_SNAPSHOT"}), timeout=1.0
        )
        # Let the fast sender drain its queue.
        await asyncio.sleep(0.05)

    # Fast client received the message; slow client's send was attempted but
    # is still pending (the sender is blocked on the never-resolving send).
    assert fast_ws.send_text.await_count >= 1
    sent = fast_ws.send_text.await_args.args[0]
    assert "USAGE_SNAPSHOT" in sent

    mgr.disconnect(slow_ws)
    mgr.disconnect(fast_ws)
    hang.cancel()


# ---------------------------------------------------------------------------
# Queue-full drop + warning (deterministic: pre-fill the queue, no await)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_broadcast_drops_and_warns_when_queue_full() -> None:
    """When a slow client's bounded send queue is full, broadcast drops the
    message (not enqueued) instead of blocking. Deterministic: pre-fill the
    queue synchronously (no await so the sender can't drain), then broadcast
    once — the put_nowait raises QueueFull → drop. Assert on the queue state
    (still full, the new message was NOT enqueued) rather than log capture,
    since structlog doesn't propagate to stdlib ``caplog`` in this config."""
    mgr = ConnectionManager()
    hang: asyncio.Future[None] = asyncio.Future()
    slow_ws = _make_ws(send_side_effect=hang)
    await mgr.connect_system(slow_ws)
    conn = mgr.connection_senders[slow_ws]

    # Pre-fill the queue synchronously — the sender task has not been
    # scheduled yet (no await between put_nowait calls), so it can't drain.
    for _ in range(conn.queue.maxsize):
        conn.queue.put_nowait("pending")
    assert conn.queue.full()

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("roboco.api.websocket.SEND_TIMEOUT_SECONDS", 0.1)
        # Must not raise and must not block.
        await asyncio.wait_for(
            mgr.broadcast_system({"type": "RATE_LIMIT_HIT"}), timeout=1.0
        )

    # The broadcast was dropped: the queue still holds exactly maxsize items
    # (the new message was NOT enqueued — put_nowait raised QueueFull).
    assert conn.queue.full()
    assert conn.queue.qsize() == conn.queue.maxsize

    mgr.disconnect(slow_ws)
    hang.cancel()


# ---------------------------------------------------------------------------
# Fast registered client receives the message (happy path)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_broadcast_delivers_to_registered_fast_client() -> None:
    """A registered connection with a fast send_text receives the message
    via its sender task."""
    mgr = ConnectionManager()
    fast_ws = _make_ws()
    await mgr.connect_system(fast_ws)

    await mgr.broadcast_system({"type": "RATE_LIMIT_HIT", "provider": "anthropic"})
    # Let the sender drain.
    await asyncio.sleep(0.05)

    assert fast_ws.send_text.await_count == 1
    sent = fast_ws.send_text.await_args.args[0]
    assert "RATE_LIMIT_HIT" in sent
    assert "anthropic" in sent

    mgr.disconnect(fast_ws)


# ---------------------------------------------------------------------------
# Legacy fallback: unregistered socket in a subscription set still gets a
# send timeout (so the OLD direct-send path is also protected).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_broadcast_send_timeout_protects_legacy_unregistered_socket() -> None:
    """A socket present in a subscription set but NOT registered via connect_*
    (the legacy test path) still must not block broadcast forever: the
    fallback wraps send_text in wait_for(SEND_TIMEOUT_SECONDS)."""
    mgr = ConnectionManager()
    hang: asyncio.Future[None] = asyncio.Future()

    async def _hang() -> None:
        await hang

    legacy_ws = MagicMock()
    legacy_ws.send_text = _hang
    # Put it straight into the set — bypasses connect_system (no sender).
    mgr.system_connections.add(legacy_ws)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("roboco.api.websocket.SEND_TIMEOUT_SECONDS", 0.1)
        # Must return in well under 1s — the slow send is timed out, not
        # awaited indefinitely.
        await asyncio.wait_for(
            mgr.broadcast_system({"type": "RATE_LIMIT_HIT"}), timeout=1.0
        )

    hang.cancel()


# ---------------------------------------------------------------------------
# Send-side failure proactively reaps the dead socket.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sender_triggers_disconnect_on_send_error() -> None:
    """When ``send_text`` raises (transport closed / dead socket), the sender
    task must proactively disconnect the socket from every subscription set
    rather than wait for the receive loop's idle timeout — otherwise a
    send-side-detected dead socket lingers and broadcasts keep enqueuing into
    a queue whose consumer has exited."""
    mgr = ConnectionManager()
    dead_ws = _make_ws(send_side_effect=ConnectionError("transport closed"))
    await mgr.connect_system(dead_ws)
    assert dead_ws in mgr.system_connections
    assert dead_ws in mgr.connection_senders

    await mgr.broadcast_system({"type": "RATE_LIMIT_HIT"})
    # Let the sender drain the queue and hit the send error.
    await asyncio.sleep(0.05)

    # The send error triggered disconnect: the dead socket is gone from every
    # subscription set + the sender registry, so future broadcasts skip it
    # entirely instead of enqueueing into an un-drained queue.
    assert dead_ws not in mgr.system_connections
    assert dead_ws not in mgr.connection_senders


@pytest.mark.asyncio
async def test_sender_keeps_live_socket_on_send_timeout_only() -> None:
    """A send TIMEOUT alone (slow client, not a dead socket) must NOT disconnect
    the socket — only a hard send Exception (transport closed) does. A
    slow-but-live client should keep receiving once it drains; timing it out
    is the graceful-degradation path, not a reap trigger."""
    mgr = ConnectionManager()
    hang: asyncio.Future[None] = asyncio.Future()
    slow_ws = _make_ws(send_side_effect=hang)
    await mgr.connect_system(slow_ws)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("roboco.api.websocket.SEND_TIMEOUT_SECONDS", 0.1)
        await mgr.broadcast_system({"type": "RATE_LIMIT_HIT"})
        await asyncio.sleep(0.2)

    # Timed out, but the socket is still live (registered) — slow, not dead.
    assert slow_ws in mgr.system_connections
    assert slow_ws in mgr.connection_senders

    mgr.disconnect(slow_ws)
    hang.cancel()


# ---------------------------------------------------------------------------
# disconnect cancels the sender task (no leak)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disconnect_cancels_sender_task() -> None:
    """disconnect() cancels the per-connection sender task so it doesn't
    leak after the socket is removed."""
    mgr = ConnectionManager()
    ws = _make_ws()
    await mgr.connect_system(ws)
    conn = mgr.connection_senders[ws]
    sender = conn.sender
    assert sender is not None
    assert not sender.cancelled()

    mgr.disconnect(ws)

    # Sender is removed + cancelled (or done). Give the loop a tick so the
    # cancellation actually propagates (cancel() schedules, doesn't sync).
    assert ws not in mgr.connection_senders
    with contextlib.suppress(TimeoutError, asyncio.CancelledError):
        await asyncio.wait_for(sender, timeout=1.0)
    assert sender.cancelled() or sender.done()


# ---------------------------------------------------------------------------
# Existing direct-set subscription-set broadcast still works (backward compat)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_broadcast_to_legacy_direct_set_sends_to_each_socket() -> None:
    """Sockets added directly to a subscription set (not via connect_*) are
    still sent to via the fallback path — preserves the existing test contract."""
    mgr = ConnectionManager()
    ws1, ws2 = MagicMock(), MagicMock()
    ws1.send_text = AsyncMock()
    ws2.send_text = AsyncMock()
    mgr.system_connections = {ws1, ws2}

    await mgr.broadcast_system({"type": "x"})
    # The fallback schedules a timeout-bounded send task per socket; let them
    # run to completion before asserting.
    await asyncio.sleep(0.05)

    ws1.send_text.assert_awaited_once()
    ws2.send_text.assert_awaited_once()
