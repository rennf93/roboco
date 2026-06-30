"""Coverage for roboco.events.bus thin wrapper functions."""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.events.bus import EventBus, get_event_bus, init_event_bus
from roboco.events.stream_bus import StreamEventBus
from roboco.models.events import Event, EventType

if TYPE_CHECKING:
    from redis.asyncio import Redis


def test_get_event_bus_delegates() -> None:
    """get_event_bus() returns the underlying stream event bus singleton."""
    fake_bus = MagicMock()
    with patch(
        "roboco.events.bus.get_stream_event_bus", return_value=fake_bus
    ) as mock_get:
        result = get_event_bus()
    mock_get.assert_called_once()
    assert result is fake_bus


@pytest.mark.asyncio
async def test_init_event_bus_delegates() -> None:
    """init_event_bus() forwards args to init_stream_event_bus (line 53)."""
    fake_bus = MagicMock()
    with patch(
        "roboco.events.bus.init_stream_event_bus",
        new_callable=AsyncMock,
        return_value=fake_bus,
    ) as mock_init:
        result = await init_event_bus(consumer_name="custom", recover_pending=False)
    mock_init.assert_awaited_once_with(consumer_name="custom", recover_pending=False)
    assert result is fake_bus


def test_event_bus_alias_is_stream_event_bus() -> None:
    """EventBus is an alias for StreamEventBus."""

    assert EventBus is StreamEventBus


# --- #19: replayed events must not re-run already-succeeded handlers ---


class _FakeRedis:
    """In-memory stand-in for the redis client's SET-NX + DELETE surface.

    ``set(..., nx=True)`` returns True the first time a key is set, None if it
    already exists (matches redis-py). ``delete`` removes a key. ``get`` is
    unused but kept for completeness.
    """

    def __init__(self) -> None:
        self.keys: dict[str, str] = {}
        self.set_calls: list[tuple[str, bool]] = []

    async def set(
        self, key: str, value: str, *, nx: bool = False, ex: int | None = None
    ) -> bool | None:
        del ex
        self.set_calls.append((key, nx))
        if nx:
            if key in self.keys:
                return None
            self.keys[key] = value
            return True
        self.keys[key] = value
        return True

    async def delete(self, key: str) -> int:
        return 1 if self.keys.pop(key, None) is not None else 0

    async def get(self, key: str) -> str | None:
        return self.keys.get(key)


@pytest.mark.asyncio
async def test_dispatch_skips_handler_that_already_succeeded_on_replay() -> None:
    """#19: ``recover_pending`` re-delivers a message whose ACK was blocked by a
    sibling handler's failure. A handler that already succeeded for that event
    must NOT re-run (no duplicate side effects) — the bus marks each
    (event.id, handler) processed via a SET-NX guard."""

    bus = StreamEventBus()
    bus._redis = cast("Redis", _FakeRedis())

    calls: list[str] = []

    async def _handler(_event: Event) -> None:
        calls.append("ran")

    bus.subscribe(EventType.NOTIFICATION_SENT, _handler)

    event = Event(type=EventType.NOTIFICATION_SENT, data={"task_id": "t1"})

    await bus._dispatch_event(event)  # first delivery: handler runs
    assert calls == ["ran"]

    await bus._dispatch_event(event)  # replay: handler already succeeded → skip
    assert calls == ["ran"]  # not re-run


@pytest.mark.asyncio
async def test_dispatch_reruns_handler_that_failed_on_first_attempt() -> None:
    """#19: a handler that failed on the first delivery must NOT be marked
    processed — a replay re-runs it (the SET-NX key is cleared on failure)."""

    bus = StreamEventBus()
    bus._redis = cast("Redis", _FakeRedis())

    attempts: list[str] = []

    async def _flaky(_event: Event) -> None:
        attempts.append("ran")
        if len(attempts) == 1:
            raise RuntimeError("transient blow-up")

    bus.subscribe(EventType.NOTIFICATION_SENT, _flaky)

    event = Event(type=EventType.NOTIFICATION_SENT, data={"task_id": "t2"})
    first = await bus._dispatch_event(event)  # fails → not marked processed
    assert first is False
    assert attempts == ["ran"]

    await bus._dispatch_event(event)  # replay: re-run (failed before)
    assert attempts == ["ran", "ran"]


@pytest.mark.asyncio
async def test_dispatch_runs_handler_when_redis_guard_unavailable() -> None:
    """#19: the idempotency guard is best-effort — with no redis the bus must
    still run the handler (fail-open: never skip a handler because the dedup
    infra is down)."""

    bus = StreamEventBus()
    # No redis connected — guard is skipped, handler runs normally.
    assert bus._redis is None

    calls: list[str] = []

    async def _handler(_event: Event) -> None:
        calls.append("ran")

    bus.subscribe(EventType.NOTIFICATION_SENT, _handler)
    event = Event(type=EventType.NOTIFICATION_SENT, data={"task_id": "t3"})

    ok = await bus._dispatch_event(event)
    assert ok is True
    assert calls == ["ran"]


# --- poison-pill: an undecodable message must be ACKed, not retried forever ---


class _FakeRedisStream:
    """In-memory redis surface for the xack/xadd/group path.

    Tracks ACKs and dead-letter xadds so the poison-pill test can assert a
    malformed message is acknowledged (not left pending) and parked on the
    dead-letter stream. ``xreadgroup`` yields nothing so a listen loop never
    spins.
    """

    def __init__(self) -> None:
        self.xack_calls: list[tuple[str, str, tuple[str, ...]]] = []
        self.xadd_calls: list[tuple[str, dict]] = []

    async def xack(self, stream: str, group: str, *ids: str) -> int:
        self.xack_calls.append((stream, group, ids))
        return len(ids)

    async def xadd(
        self,
        stream: str,
        fields: dict,
        maxlen: int | None = None,
        approximate: bool = True,
    ) -> bytes:
        del maxlen, approximate
        self.xadd_calls.append((stream, dict(fields)))
        return b"1-0"

    async def xreadgroup(self, *args: object, **kwargs: object) -> list:
        del args, kwargs
        return []

    async def xgroup_create(self, *args: object, **kwargs: object) -> bool:
        del args, kwargs
        return True

    async def xpending(self, *args: object, **kwargs: object) -> dict:
        del args, kwargs
        return {"pending": 0}

    async def xpending_range(self, *args: object, **kwargs: object) -> list:
        del args, kwargs
        return []

    async def xclaim(self, *args: object, **kwargs: object) -> list:
        del args, kwargs
        return []

    async def set(
        self, key: str, value: str, *, nx: bool = False, ex: int | None = None
    ) -> bool:
        del key, value, nx, ex
        return True

    async def delete(self, key: str) -> int:
        del key
        return 1

    async def get(self, key: str) -> None:
        del key

    async def close(self) -> None:
        """No-op close for the fake client."""


@pytest.mark.asyncio
async def test_undecodable_message_is_acked_and_dead_lettered() -> None:
    """A message whose payload fails Event.from_json (unknown EventType value,
    bad UUID, malformed JSON) is a poison pill: no handler could ever process
    it, so retrying is pointless. The bus must ACK it (and dead-letter it) so
    the stream doesn't wedge on an unkillable pending message re-failing on
    every reclaim."""

    bus = StreamEventBus()
    fake = _FakeRedisStream()
    bus._redis = cast("Redis", fake)

    invoked: list[str] = []

    async def _handler(_event: Event) -> None:
        invoked.append("ran")

    bus.subscribe(EventType.NOTIFICATION_SENT, _handler)

    # type="task.bogus" is not a real EventType → EventType(...) raises ValueError
    # inside Event.from_json.
    malformed = json.dumps(
        {
            "id": "not-a-uuid",
            "type": "task.bogus_unknown",
            "data": {},
            "timestamp": "2026-06-30T00:00:00+00:00",
        }
    )
    await bus._handle_message("roboco:stream:task", "1234-0", {b"data": malformed})

    # ACKed exactly once — not left pending for reclaim to re-fail forever.
    assert len(fake.xack_calls) == 1
    assert fake.xack_calls[0][2] == ("1234-0",)
    # Dead-lettered for inspection before the ACK.
    assert len(fake.xadd_calls) == 1
    assert fake.xadd_calls[0][0] == StreamEventBus.DEAD_LETTER_STREAM
    # No handler could run — the event never decoded.
    assert invoked == []


# --- periodic reclaim: a runtime handler failure is retried without a restart ---


@pytest.mark.asyncio
async def test_reclaim_loop_periodically_calls_recover_pending() -> None:
    """XREADGROUP '>' delivers only NEW messages, so a handler that fails at
    runtime leaves its message pending and unretried until the orchestrator
    restarts. A periodic reclaim loop must call recover_pending so the
    idempotency-guarded replay actually fires."""

    bus = StreamEventBus()
    bus._running = True
    bus._reclaim_interval = 60

    calls: list[int] = []

    async def _fake_recover(idle_time_ms: int = 60000) -> int:
        calls.append(idle_time_ms)
        bus._running = False  # break the loop after the first reclaim
        return 0

    bus.recover_pending = _fake_recover  # type: ignore[method-assign]

    async def _no_sleep(_seconds: float) -> None:
        return

    with patch("roboco.events.stream_bus.asyncio.sleep", new=_no_sleep):
        await bus._reclaim_loop()

    # Reclaim ran once with the interval-aligned idle window, then the loop exited.
    assert calls == [60000]


@pytest.mark.asyncio
async def test_start_listening_spawns_reclaim_task_alongside_listen() -> None:
    """start_listening must spawn the reclaim task, not just the listen task —
    otherwise pending messages are never re-delivered at runtime."""

    bus = StreamEventBus()
    bus._redis = cast("Redis", _FakeRedisStream())

    async def _noop(self: StreamEventBus) -> None:
        del self

    async def _handler(_event: Event) -> None: ...

    bus.subscribe(EventType.NOTIFICATION_SENT, _handler)

    with (
        patch.object(StreamEventBus, "_listen_loop", _noop),
        patch.object(StreamEventBus, "_reclaim_loop", _noop),
    ):
        await bus.start_listening()

    try:
        assert bus._listen_task is not None
        assert bus._reclaim_task is not None
    finally:
        await bus.disconnect()


# --- cancellation mid-handler must clear the idempotency marker ---


@pytest.mark.asyncio
async def test_cancelled_handler_clears_idempotency_marker() -> None:
    """A handler cancelled mid-flight (shutdown / sibling gather cancellation)
    is BaseException-cancelled, not Exception-raised, so the old ``except
    Exception`` left the SET-NX marker set: the message stayed pending but the
    guard then suppressed the very redelivery that would complete the work.
    The cleanup must catch BaseException so the marker is cleared and reclaim
    re-runs the handler."""

    bus = StreamEventBus()
    fake = _FakeRedis()
    bus._redis = cast("Redis", fake)

    started = asyncio.Event()
    proceed = asyncio.Event()

    async def _blocking(_event: Event) -> None:
        started.set()
        await proceed.wait()  # block until the dispatch task is cancelled

    bus.subscribe(EventType.NOTIFICATION_SENT, _blocking)
    event = Event(type=EventType.NOTIFICATION_SENT, data={"task_id": "tc"})

    task = asyncio.create_task(bus._dispatch_event(event))
    await started.wait()  # handler is now blocked → marker is set

    key = f"bus:processed:{event.id}:_blocking"
    assert key in fake.keys  # marker acquired before the handler blocked

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    # Marker cleared despite cancellation → a replay re-runs the handler.
    assert key not in fake.keys
