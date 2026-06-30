"""Coverage for roboco.events.bus thin wrapper functions."""

from __future__ import annotations

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
