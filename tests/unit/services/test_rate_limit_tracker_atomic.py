"""The probe-failure counter update must be a single atomic Redis op.

``increment_probe_failures`` / ``reset_probe_failures`` used to do a non-atomic
read-modify-write: ``get_state`` (GET) → mutate the dict → ``SET`` the whole
blob back. A concurrent ``activate()`` (a re-park — grok 429 re-park, a 529
overload re-park) writes a FRESH episode blob (``probe_failures: 0`` + fresh
``activated_at`` / ``retry_after`` / ``affected_agents`` / ``kind``). If the
stale ``increment``'s SET lands AFTER the fresh ``activate``'s SET, the stale
blob overwrites the fresh episode metadata AND un-resets the counter (writes
back the old ``probe_failures`` + old metadata) — clobbering the new episode.

Redis single-threads a Lua ``EVAL``, so a server-side read-modify-write is
indivisible: ``activate``'s ``SET`` is serialized entirely before or entirely
after the script — never interleaved between the script's GET and SET. These
tests pin the WIRING (the increment/reset go through ``eval``, a single atomic
server-side call, NOT a separate ``get``+``set`` pair) and the field-preservation
(the script decodes, mutates ONLY ``probe_failures``, re-encodes — every other
episode field survives the bump).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from roboco.services.gateway.rate_limit_tracker import RateLimitStateTracker


def _make_redis_mock(initial_store: dict[str, Any] | None = None) -> AsyncMock:
    """Async Redis mock backed by a dict, with server-side ``eval`` for the
    tracker's two Lua scripts.

    The ``eval`` impl mirrors the Lua (GET → decode → mutate only
    ``probe_failures`` → SET) so a single-threaded test observes the same result
    production gets from Redis' atomic Lua execution. Real concurrency cannot be
    simulated with a mock; the atomicity guarantee in production is Redis'
    single-threaded Lua, which these tests pin by asserting the tracker routes
    through ``eval`` (one atomic op) rather than a separate ``get``+``set``.
    """
    store: dict[str, Any] = initial_store if initial_store is not None else {}

    async def _get(key: str) -> bytes | None:
        val = store.get(key)
        if val is None:
            return None
        if isinstance(val, bytes):
            return val
        return str(val).encode()

    async def _set(key: str, value: Any) -> None:
        store[key] = value

    async def _delete(key: str) -> int:
        return 1 if store.pop(key, None) is not None else 0

    async def _eval(script: str, _numkeys: int, *keys_and_args: Any) -> Any:
        key = keys_and_args[0]
        raw = store.get(key)
        text = raw.decode() if isinstance(raw, bytes) else (str(raw) if raw else None)
        if "roboco:increment_probe_failures" in script:
            if text is None:
                state: dict[str, Any] = {"probe_failures": 1}
            else:
                state = json.loads(text)
                state["probe_failures"] = state.get("probe_failures", 0) + 1
            store[key] = json.dumps(state)
            return state["probe_failures"]
        if "roboco:reset_probe_failures" in script:
            if text is None:
                state = {"probe_failures": 0}
            else:
                state = json.loads(text)
                state["probe_failures"] = 0
            store[key] = json.dumps(state)
            return None
        if "roboco:activate_rate_limit" in script:
            # Mirror the production merge: decode the fresh episode blob from
            # ARGV[1], carry over the previous probe_failures if a blob exists,
            # else keep the fresh probe_failures (0). Atomic vs increment/reset.
            fresh = json.loads(keys_and_args[1])
            if text is not None:
                prev = json.loads(text)
                if "probe_failures" in prev and prev["probe_failures"] is not None:
                    fresh["probe_failures"] = prev["probe_failures"]
            store[key] = json.dumps(fresh)
            return fresh.get("probe_failures", 0)
        raise AssertionError(f"unknown eval script: {script[:80]}")

    mock = AsyncMock()
    mock.get = AsyncMock(side_effect=_get)
    mock.set = AsyncMock(side_effect=_set)
    mock.delete = AsyncMock(side_effect=_delete)
    mock.eval = AsyncMock(side_effect=_eval)
    mock._store = store
    return mock


def _make_tracker(redis_mock: AsyncMock) -> RateLimitStateTracker:
    tracker = RateLimitStateTracker(provider="anthropic", redis_url="redis://unused")
    tracker._redis = redis_mock
    return tracker


@pytest.mark.asyncio
async def test_increment_uses_atomic_eval_not_separate_get_set() -> None:
    """The increment must route through ``eval`` (one atomic server-side op) and
    must NOT issue a separate ``set`` for the read-modify-write — the separate
    SET is exactly the non-atomic write a concurrent ``activate`` can clobber."""
    mock = _make_redis_mock()
    tracker = _make_tracker(mock)
    await tracker.activate(retry_after=60.0, affected_agents=["be-dev-1"])
    # activate routes through its own atomic eval; reset call counts so the
    # increment path's commands are isolated.
    mock.set.reset_mock()
    mock.get.reset_mock()
    mock.eval.reset_mock()

    count = await tracker.increment_probe_failures()

    assert count == 1
    mock.eval.assert_awaited_once()
    # The atomic op is server-side — the tracker must not issue its own SET
    # (a separate SET is the non-atomic write a racing activate clobbers).
    mock.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_reset_uses_atomic_eval_not_separate_get_set() -> None:
    mock = _make_redis_mock()
    tracker = _make_tracker(mock)
    await tracker.activate()
    await tracker.increment_probe_failures()
    mock.set.reset_mock()
    mock.get.reset_mock()
    mock.eval.reset_mock()

    await tracker.reset_probe_failures()

    mock.eval.assert_awaited_once()
    mock.set.assert_not_awaited()
    assert (await tracker.get_state())["probe_failures"] == 0


@pytest.mark.asyncio
async def test_increment_preserves_episode_metadata() -> None:
    """The atomic script decodes, mutates ONLY ``probe_failures``, and re-encodes
    — every other episode field (rate_limited / kind / activated_at / retry_after
    / affected_agents) survives the bump. This is the property a non-atomic
    GET+SET that read a STALE blob would violate under a concurrent activate."""
    retry_after = 120.0
    bumps = 2
    mock = _make_redis_mock()
    tracker = _make_tracker(mock)
    await tracker.activate(
        retry_after=retry_after,
        affected_agents=["be-dev-1", "fe-dev-1"],
        kind="overloaded",
    )
    before = await tracker.get_state()

    for _ in range(bumps):
        await tracker.increment_probe_failures()

    after = await tracker.get_state()
    assert after["probe_failures"] == bumps
    # Episode metadata untouched by the counter bump.
    assert after["rate_limited"] is before["rate_limited"] is True
    assert after["kind"] == "overloaded"
    assert after["retry_after"] == retry_after
    assert after["affected_agents"] == ["be-dev-1", "fe-dev-1"]
    assert after["activated_at"] == before["activated_at"]


@pytest.mark.asyncio
async def test_increment_accumulates_across_calls() -> None:
    mock = _make_redis_mock()
    tracker = _make_tracker(mock)
    await tracker.activate()
    total = 4
    counts = [await tracker.increment_probe_failures() for _ in range(total)]
    assert counts == [1, 2, 3, total]
    assert (await tracker.get_state())["probe_failures"] == total


@pytest.mark.asyncio
async def test_reset_zeroes_after_increments() -> None:
    mock = _make_redis_mock()
    tracker = _make_tracker(mock)
    await tracker.activate()
    for _ in range(3):
        await tracker.increment_probe_failures()
    await tracker.reset_probe_failures()
    assert (await tracker.get_state())["probe_failures"] == 0


# ---------------------------------------------------------------------------
# #156: activate must MERGE probe_failures, not blind-SET the whole blob.
# ---------------------------------------------------------------------------

_INITIAL_RETRY_AFTER = 60.0
_REPARK_RETRY_AFTER = 300.0
_PROBE_FAILURES_BEFORE_REPARK = 8


@pytest.mark.asyncio
async def test_activate_routes_through_atomic_eval() -> None:
    """activate must go through ``eval`` (one atomic server-side op), not a
    separate ``get``+``set`` — a non-atomic activate is exactly the blind write
    whose SET can land after a racing increment's SET and reset the count to 0.
    The merge runs server-side so it is indivisible w.r.t. increment/reset."""
    mock = _make_redis_mock()
    tracker = _make_tracker(mock)
    await tracker.activate(
        retry_after=_INITIAL_RETRY_AFTER, affected_agents=["be-dev-1"]
    )
    mock.eval.assert_awaited_once()
    mock.set.assert_not_awaited()


@pytest.mark.asyncio
async def test_activate_fresh_starts_probe_failures_at_zero() -> None:
    """With no prior blob there is nothing to merge — activate starts a fresh
    episode at probe_failures=0 and writes the full episode metadata."""
    mock = _make_redis_mock()
    tracker = _make_tracker(mock)
    await tracker.activate(
        retry_after=_INITIAL_RETRY_AFTER,
        affected_agents=["be-dev-1"],
        kind="rate_limited",
    )
    state = await tracker.get_state()
    assert state["probe_failures"] == 0
    assert state["rate_limited"] is True
    assert state["kind"] == "rate_limited"
    assert state["retry_after"] == _INITIAL_RETRY_AFTER
    assert state["affected_agents"] == ["be-dev-1"]


@pytest.mark.asyncio
async def test_activate_preserves_probe_failures_on_repark() -> None:
    """#156: a re-park (activate) used to blind-SET a fresh blob with
    probe_failures=0, so a probe-failure increment that just landed (or was in
    flight) could be wiped by the concurrent re-park — resetting the give-up /
    CEO-notify count mid-episode. activate must MERGE: refresh the episode
    metadata (kind / activated_at / retry_after / affected_agents) while
    preserving the accumulated probe_failures count."""
    mock = _make_redis_mock()
    tracker = _make_tracker(mock)
    await tracker.activate(
        retry_after=_INITIAL_RETRY_AFTER,
        affected_agents=["be-dev-1"],
        kind="rate_limited",
    )
    for _ in range(_PROBE_FAILURES_BEFORE_REPARK):
        await tracker.increment_probe_failures()
    assert (await tracker.get_state())[
        "probe_failures"
    ] == _PROBE_FAILURES_BEFORE_REPARK

    await tracker.activate(
        retry_after=_REPARK_RETRY_AFTER,
        affected_agents=["be-dev-1", "fe-dev-1"],
        kind="overloaded",
    )

    state = await tracker.get_state()
    assert state["probe_failures"] == _PROBE_FAILURES_BEFORE_REPARK  # preserved, not 0
    # episode metadata refreshed by the re-park
    assert state["kind"] == "overloaded"
    assert state["retry_after"] == _REPARK_RETRY_AFTER
    assert state["affected_agents"] == ["be-dev-1", "fe-dev-1"]
    assert state["rate_limited"] is True
    # a subsequent increment continues from the preserved count
    assert await tracker.increment_probe_failures() == _PROBE_FAILURES_BEFORE_REPARK + 1


@pytest.mark.asyncio
async def test_activate_repark_after_reset_keeps_zero() -> None:
    """A re-park arriving after the counter was explicitly reset (probe_failures
    already 0) carries over 0 — merge never manufactures a positive count."""
    mock = _make_redis_mock()
    tracker = _make_tracker(mock)
    await tracker.activate()
    for _ in range(5):
        await tracker.increment_probe_failures()
    await tracker.reset_probe_failures()
    await tracker.activate(
        retry_after=_INITIAL_RETRY_AFTER, affected_agents=["be-dev-1"]
    )
    assert (await tracker.get_state())["probe_failures"] == 0
