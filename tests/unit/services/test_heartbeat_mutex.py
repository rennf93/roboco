"""HeartbeatMutex coverage: acquire (fencing token), heartbeat renew,
compare-and-del release, fail-closed on a Redis outage, and the
run_guarded cancel-on-lock-loss dance.

No live Redis in tests (matches the project's `_no_live_redis` fixture); a
tiny in-memory fake backs the Lua compare-and-del/compare-and-expire scripts
so the fencing semantics are observable without a real Redis.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.services.heartbeat_mutex import (
    HeartbeatLockUnavailable,
    HeartbeatMutex,
)

_KEY = "roboco:test_mutex:abc"
_MIN_RENEWS_AFTER_RECOVERY = 2  # the failed renew, then >=1 recovered one


class _FakeRedis:
    """In-memory single-key store backing the mutex's SET NX EX + two Lua
    scripts (compare-and-del release, compare-and-expire heartbeat)."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self.set_calls: list[tuple[str, str, bool, int]] = []
        self.eval_calls: list[tuple[str, tuple[Any, ...]]] = []

    async def set(
        self, name: str, value: str, *, nx: bool = False, ex: int = 0
    ) -> bool:
        self.set_calls.append((name, value, nx, ex))
        if nx and name in self._store:
            return False
        self._store[name] = value
        return True

    async def eval(self, script: str, _numkeys: int, *args: Any) -> int:
        self.eval_calls.append((script, args))
        key, token = args[0], args[1]
        if "expire" in script:
            return 1 if self._store.get(key) == token else 0
        if self._store.get(key) == token:
            del self._store[key]
            return 1
        return 0

    async def aclose(self) -> None:
        return None


def _mutex(*, ttl: int = 60, heartbeat: float = 30.0) -> HeartbeatMutex:
    return HeartbeatMutex(_KEY, ttl_seconds=ttl, heartbeat_seconds=heartbeat)


@pytest.mark.asyncio
async def test_acquire_sets_nx_ex_and_returns_a_fencing_token() -> None:
    fake = _FakeRedis()
    with patch("roboco.services.heartbeat_mutex.redis.from_url", return_value=fake):
        token = await _mutex(ttl=1800).acquire()
    assert token is not None
    assert fake.set_calls == [(_KEY, token, True, 1800)]


@pytest.mark.asyncio
async def test_acquire_returns_none_when_already_held() -> None:
    fake = _FakeRedis()
    with patch("roboco.services.heartbeat_mutex.redis.from_url", return_value=fake):
        first = await _mutex().acquire()
        second = await _mutex().acquire()
    assert first is not None
    assert second is None


@pytest.mark.asyncio
async def test_release_is_compare_and_del_spares_a_usurper_lock() -> None:
    fake = _FakeRedis()
    with patch("roboco.services.heartbeat_mutex.redis.from_url", return_value=fake):
        mutex = _mutex()
        token = await mutex.acquire()
        assert token is not None
        # A usurper re-acquired after this token's TTL expired.
        fake._store[_KEY] = "usurper-token"
        await mutex.release(token)
        assert fake._store.get(_KEY) == "usurper-token"  # survives a stale release
        await mutex.release("usurper-token")
        assert _KEY not in fake._store  # the owning token does clear it


@pytest.mark.asyncio
async def test_heartbeat_once_true_when_owned_false_otherwise() -> None:
    fake = _FakeRedis()
    with patch("roboco.services.heartbeat_mutex.redis.from_url", return_value=fake):
        mutex = _mutex()
        token = await mutex.acquire()
        assert token is not None
        assert await mutex.heartbeat_once(token) is True
        assert await mutex.heartbeat_once("wrong-token") is False


@pytest.mark.asyncio
async def test_acquire_raises_lock_unavailable_on_redis_error() -> None:
    broken = MagicMock()
    broken.set = AsyncMock(side_effect=ConnectionError("redis down"))
    broken.aclose = AsyncMock()
    with (
        patch("roboco.services.heartbeat_mutex.redis.from_url", return_value=broken),
        pytest.raises(HeartbeatLockUnavailable),
    ):
        await _mutex().acquire()


@pytest.mark.asyncio
async def test_run_guarded_returns_the_coroutine_result_on_success() -> None:
    async def _work() -> str:
        return "done"

    mutex = _mutex(heartbeat=0.001)
    with patch.object(HeartbeatMutex, "heartbeat_once", AsyncMock(return_value=True)):
        result = await mutex.run_guarded(_work(), "tok")
    assert result.lock_lost is False
    assert result.value == "done"


@pytest.mark.asyncio
async def test_run_guarded_renews_the_ttl_while_the_work_runs() -> None:
    calls = 0

    async def _counting_heartbeat(_self: HeartbeatMutex, _token: str) -> bool:
        nonlocal calls
        calls += 1
        return True

    async def _slow_work() -> str:
        await asyncio.sleep(0.02)
        return "done"

    mutex = _mutex(heartbeat=0.001)
    with patch.object(HeartbeatMutex, "heartbeat_once", _counting_heartbeat):
        result = await mutex.run_guarded(_slow_work(), "tok")
    assert result.value == "done"
    assert calls >= 1  # at least one renew landed while the work was in flight


@pytest.mark.asyncio
async def test_run_guarded_cancels_the_work_fail_closed_on_lock_loss() -> None:
    started = asyncio.Event()

    async def _blocking_work() -> str:
        started.set()
        await asyncio.sleep(60)
        return "never"

    mutex = _mutex(heartbeat=0.001)
    with patch.object(HeartbeatMutex, "heartbeat_once", AsyncMock(return_value=False)):
        result = await mutex.run_guarded(_blocking_work(), "tok")
    assert started.is_set()  # the work did start, then got cancelled
    assert result.lock_lost is True
    assert result.value is None


@pytest.mark.asyncio
async def test_run_guarded_tolerates_a_transient_renew_raise() -> None:
    """One raised renew error, followed by recovery, must NOT trip
    lock_lost — the TTL is still alive so it's tolerated as a blip."""
    calls = 0

    async def _flaky_heartbeat(_self: HeartbeatMutex, _token: str) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ConnectionError("transient redis blip")
        return True

    async def _work() -> str:
        await asyncio.sleep(0.02)
        return "done"

    # ttl=60 vs. a sub-second test run: the grace window is enormous, so a
    # single raise is nowhere near "unable to renew for ~the whole TTL".
    mutex = _mutex(ttl=60, heartbeat=0.005)
    with patch.object(HeartbeatMutex, "heartbeat_once", _flaky_heartbeat):
        result = await mutex.run_guarded(_work(), "tok")
    assert result.lock_lost is False
    assert result.value == "done"
    assert calls >= _MIN_RENEWS_AFTER_RECOVERY


@pytest.mark.asyncio
async def test_run_guarded_fails_closed_once_renew_errors_span_the_whole_ttl() -> None:
    """A `heartbeat_once` that only ever RAISES (never returns falsy) must
    still fail closed once elapsed time since the last successful renew
    reaches ~the whole TTL — otherwise a holder stuck erroring on every
    renew would never learn its key expired server-side.

    `heartbeat_seconds > ttl_seconds` collapses the grace window
    (`ttl_seconds - heartbeat_seconds`) to <= 0, so the very first raise's
    elapsed time (always >= 0) already exceeds it — deterministic, no
    reliance on real elapsed wall-clock time (and no monkeypatching
    `time.monotonic`, which is also asyncio's own scheduling clock).
    """
    started = asyncio.Event()

    async def _blocking_work() -> str:
        started.set()
        await asyncio.sleep(60)
        return "never"

    mock_heartbeat = AsyncMock(side_effect=ConnectionError("redis down"))
    mutex = _mutex(ttl=1, heartbeat=2.0)
    with patch.object(HeartbeatMutex, "heartbeat_once", mock_heartbeat):
        result = await mutex.run_guarded(_blocking_work(), "tok")
    assert mock_heartbeat.call_count >= 1
    assert started.is_set()
    assert result.lock_lost is True
    assert result.value is None
