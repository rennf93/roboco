"""Unit tests for RateLimitStateTracker.

These tests use mock Redis clients (no real Redis server required) to
verify the state-management logic.  The cross-reconnection persistence
test constructs *two* RateLimitStateTracker instances that share the
same mock Redis store, proving that state written by one instance is
visible to a fresh instance.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from roboco.services.gateway.rate_limit_tracker import RateLimitStateTracker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_redis_mock(initial_store: dict[str, Any] | None = None) -> AsyncMock:
    """Build an async Redis mock backed by a plain dict.

    The mock supports ``get``, ``set``, and ``delete`` with the same
    semantics as the real redis.asyncio.Redis client.
    """
    # Use the dict AS-IS (no copy) so that two mocks sharing the same
    # dict object see each other's writes and deletes — this is what the
    # cross-reconnection persistence tests rely on.
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

    mock = AsyncMock()
    mock.get = AsyncMock(side_effect=_get)
    mock.set = AsyncMock(side_effect=_set)
    mock.delete = AsyncMock(side_effect=_delete)
    # Stash the backing store so tests can inspect raw state
    mock._store = store
    return mock


def _make_tracker(
    provider: str = "anthropic",
    redis_mock: AsyncMock | None = None,
) -> RateLimitStateTracker:
    """Build a tracker with an injected mock Redis client."""
    tracker = RateLimitStateTracker(provider=provider, redis_url="redis://unused")
    if redis_mock is not None:
        tracker._redis = redis_mock  # type: ignore[assignment]
    return tracker


# ---------------------------------------------------------------------------
# Tests: basic operations
# ---------------------------------------------------------------------------


class TestActivateAndRead:
    async def test_is_rate_limited_false_by_default(self) -> None:
        mock = _make_redis_mock()
        tracker = _make_tracker(redis_mock=mock)
        assert await tracker.is_rate_limited() is False

    async def test_get_state_empty_by_default(self) -> None:
        mock = _make_redis_mock()
        tracker = _make_tracker(redis_mock=mock)
        assert await tracker.get_state() == {}

    async def test_activate_sets_rate_limited_true(self) -> None:
        mock = _make_redis_mock()
        tracker = _make_tracker(redis_mock=mock)
        await tracker.activate()
        assert await tracker.is_rate_limited() is True

    async def test_activate_stores_retry_after(self) -> None:
        mock = _make_redis_mock()
        tracker = _make_tracker(redis_mock=mock)
        retry_after = 30.0
        await tracker.activate(retry_after=retry_after)
        state = await tracker.get_state()
        assert state["retry_after"] == retry_after

    async def test_activate_stores_affected_agents(self) -> None:
        mock = _make_redis_mock()
        tracker = _make_tracker(redis_mock=mock)
        await tracker.activate(affected_agents=["be-dev-1", "be-dev-2"])
        state = await tracker.get_state()
        assert state["affected_agents"] == ["be-dev-1", "be-dev-2"]

    async def test_activate_initialises_probe_failures_zero(self) -> None:
        mock = _make_redis_mock()
        tracker = _make_tracker(redis_mock=mock)
        await tracker.activate()
        state = await tracker.get_state()
        assert state["probe_failures"] == 0

    async def test_clear_removes_state(self) -> None:
        mock = _make_redis_mock()
        tracker = _make_tracker(redis_mock=mock)
        await tracker.activate()
        await tracker.clear()
        assert await tracker.is_rate_limited() is False
        assert await tracker.get_state() == {}


class TestProbeFailures:
    async def test_increment_starts_from_zero(self) -> None:
        mock = _make_redis_mock()
        tracker = _make_tracker(redis_mock=mock)
        await tracker.activate()
        count = await tracker.increment_probe_failures()
        assert count == 1

    async def test_increment_accumulates(self) -> None:
        mock = _make_redis_mock()
        tracker = _make_tracker(redis_mock=mock)
        await tracker.activate()
        increments = 3
        for _ in range(increments):
            count = await tracker.increment_probe_failures()
        assert count == increments

    async def test_reset_sets_zero(self) -> None:
        mock = _make_redis_mock()
        tracker = _make_tracker(redis_mock=mock)
        await tracker.activate()
        await tracker.increment_probe_failures()
        await tracker.increment_probe_failures()
        await tracker.reset_probe_failures()
        state = await tracker.get_state()
        assert state["probe_failures"] == 0


# ---------------------------------------------------------------------------
# Tests: cross-reconnection persistence
# ---------------------------------------------------------------------------
#
# State persists across client reconnection: a test writes state via
# activate(), creates a new RateLimitStateTracker instance pointing at the
# same Redis URL, calls is_rate_limited() and get_state() and gets back the
# same values — proving state survives a process restart.
#
# We simulate this by sharing the same backing dict between two mock Redis
# clients — one injected into the first tracker and one injected into the
# second.  Both clients read from and write to the same dict, so the second
# tracker "sees" everything the first wrote.
# ---------------------------------------------------------------------------


class TestStatePersistsAcrossReconnection:
    async def test_is_rate_limited_survives_reconnection(self) -> None:
        shared_store: dict[str, Any] = {}

        # First "connection": write rate-limit state
        mock_a = _make_redis_mock(initial_store=shared_store)
        tracker_a = _make_tracker(provider="anthropic", redis_mock=mock_a)
        await tracker_a.activate(retry_after=60.0, affected_agents=["be-dev-1"])

        # The mock writes into shared_store directly (our _set stores raw).
        # We need to seed the second mock from the same backing store.
        # Because mock_a._store IS shared_store (same dict object), we only
        # need to give mock_b access to the same dict.
        mock_b = _make_redis_mock(initial_store=mock_a._store)
        tracker_b = RateLimitStateTracker(
            provider="anthropic", redis_url="redis://unused"
        )
        tracker_b._redis = mock_b  # type: ignore[assignment]

        assert await tracker_b.is_rate_limited() is True

    async def test_get_state_survives_reconnection(self) -> None:
        shared_store: dict[str, Any] = {}

        retry_after = 45.0
        mock_a = _make_redis_mock(initial_store=shared_store)
        tracker_a = _make_tracker(provider="anthropic", redis_mock=mock_a)
        await tracker_a.activate(retry_after=retry_after, affected_agents=["be-dev-2"])

        mock_b = _make_redis_mock(initial_store=mock_a._store)
        tracker_b = RateLimitStateTracker(
            provider="anthropic", redis_url="redis://unused"
        )
        tracker_b._redis = mock_b  # type: ignore[assignment]

        state = await tracker_b.get_state()
        assert state["rate_limited"] is True
        assert state["retry_after"] == retry_after
        assert state["affected_agents"] == ["be-dev-2"]

    async def test_clear_via_first_instance_visible_to_second(self) -> None:
        shared_store: dict[str, Any] = {}

        mock_a = _make_redis_mock(initial_store=shared_store)
        tracker_a = _make_tracker(provider="anthropic", redis_mock=mock_a)
        await tracker_a.activate()

        # Second instance points at the same store
        mock_b = _make_redis_mock(initial_store=mock_a._store)
        tracker_b = RateLimitStateTracker(
            provider="anthropic", redis_url="redis://unused"
        )
        tracker_b._redis = mock_b  # type: ignore[assignment]

        # Write clear via tracker_a
        await tracker_a.clear()

        # tracker_b observes the cleared state
        assert await tracker_b.is_rate_limited() is False
        assert await tracker_b.get_state() == {}


# ---------------------------------------------------------------------------
# Tests: different providers are isolated
# ---------------------------------------------------------------------------


class TestProviderIsolation:
    async def test_activating_one_provider_does_not_affect_another(self) -> None:
        store: dict[str, Any] = {}
        mock_a = _make_redis_mock(initial_store=store)
        mock_b = _make_redis_mock(initial_store=store)

        tracker_anthropic = _make_tracker(provider="anthropic", redis_mock=mock_a)
        tracker_ollama = _make_tracker(provider="ollama_cloud", redis_mock=mock_b)

        await tracker_anthropic.activate()
        assert await tracker_anthropic.is_rate_limited() is True
        assert await tracker_ollama.is_rate_limited() is False
