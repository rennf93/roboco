"""Unit tests for the rate-limit sweeper probe loop.

Tests cover:
- probe-success path: tracker.clear() + resolve_wait + RATE_LIMIT_LIFTED event
- probe-failure path: increment_probe_failures is called
- CEO notification fires at threshold 10 exactly once per episode
- ``_do_probe`` / ``_make_tracker`` are injectable boundaries for mocking
"""

from __future__ import annotations

import fnmatch
import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from httpx import ASGITransport, AsyncClient
from roboco.api.app import create_app
from roboco.models.events import EventType
from roboco.models.runtime import WaitingRecord
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.services.gateway.rate_limit_tracker import RateLimitStateTracker

_HTTP_OK = 200
_HTTP_NOT_FOUND = 404

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_redis_mock(initial_store: dict[str, Any] | None = None) -> AsyncMock:
    """Fake redis.asyncio.Redis backed by a plain dict."""
    store: dict[str, Any] = initial_store if initial_store is not None else {}

    async def _get(key: str) -> bytes | None:
        val = store.get(key)
        if val is None:
            return None
        return str(val).encode() if not isinstance(val, bytes) else val

    async def _set(key: str, value: Any) -> None:
        store[key] = value

    async def _delete(key: str) -> int:
        return 1 if store.pop(key, None) is not None else 0

    async def _scan(
        _cursor: int,
        match: str = "*",
        count: int = 100,  # noqa: ARG001
    ) -> tuple[int, list[bytes]]:
        # Simple in-memory scan: return all matching keys in one shot
        matches = [k.encode() for k in store if fnmatch.fnmatch(k, match)]
        return (0, matches)

    async def _aclose() -> None:
        pass

    mock = AsyncMock()
    mock.get = AsyncMock(side_effect=_get)
    mock.set = AsyncMock(side_effect=_set)
    mock.delete = AsyncMock(side_effect=_delete)
    mock.scan = AsyncMock(side_effect=_scan)
    mock.aclose = AsyncMock(side_effect=_aclose)
    # Support `async with redis.from_url(...) as r:` — the client returns
    # itself on enter so the configured side-effects are what the caller uses.
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    mock._store = store
    return mock


def _make_orchestrator() -> AgentOrchestrator:
    """Build a minimal orchestrator via __new__ (no __init__ side-effects)."""
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._running = True
    orch._waiting_records = {}
    orch._instances = {}
    orch._rate_limit_ceo_notified = set()
    return orch


def _make_tracker_mock(failure_return: int = 1) -> AsyncMock:
    """Create an async mock RateLimitStateTracker instance."""
    mock = AsyncMock()
    mock.clear = AsyncMock()
    mock.increment_probe_failures = AsyncMock(return_value=failure_return)
    mock.reset_probe_failures = AsyncMock()
    return mock


def _make_active_state(
    _provider: str = "anthropic",
    retry_after: float | None = None,
    probe_failures: int = 0,
    activated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build a tracker state dict."""
    at = activated_at or datetime.now(UTC)
    return {
        "rate_limited": True,
        "activated_at": at.isoformat(),
        "retry_after": retry_after,
        "affected_agents": ["be-dev-1"],
        "probe_failures": probe_failures,
    }


def _waiting_record(
    agent_id: str,
    provider: str = "anthropic",
    task_id: str | None = None,
) -> WaitingRecord:
    return WaitingRecord(
        agent_id=agent_id,
        task_id=task_id or str(uuid4()),
        waiting_for="rate_limit_lifted",
        waiting_since=datetime.now(UTC),
        context={"provider": provider},
    )


# ---------------------------------------------------------------------------
# Tests: probe-success path
# ---------------------------------------------------------------------------


class TestProbeSuccessPath:
    """When _do_probe returns True the rate limit should be cleared and
    all parked agents resolved."""

    async def test_tracker_clear_called_on_success(self) -> None:
        """tracker.clear() is invoked when the probe succeeds."""
        orch = _make_orchestrator()
        provider = "anthropic"
        state = _make_active_state(provider, retry_after=None)

        tracker_mock = _make_tracker_mock()

        with (
            patch.object(orch, "_make_tracker", return_value=tracker_mock),
            patch.object(orch, "resolve_wait", new=AsyncMock(return_value=None)),
            patch.object(orch, "_do_probe", new=AsyncMock(return_value=True)),
            patch("roboco.events.get_event_bus") as mock_bus_fn,
        ):
            bus_mock = AsyncMock()
            bus_mock.publish = AsyncMock()
            mock_bus_fn.return_value = bus_mock

            await orch._probe_one_provider(provider, state)

        tracker_mock.clear.assert_awaited_once()

    async def test_resolve_wait_called_for_parked_agents(self) -> None:
        """resolve_wait is called for each agent waiting for rate_limit_lifted."""
        orch = _make_orchestrator()
        provider = "anthropic"
        state = _make_active_state(provider, retry_after=None)

        agent1 = "be-dev-1"
        agent2 = "be-dev-2"
        orch._waiting_records = {
            agent1: _waiting_record(agent1, provider),
            agent2: _waiting_record(agent2, provider),
            "be-qa-1": _waiting_record(
                "be-qa-1", "other-provider"
            ),  # different provider
        }

        resolve_mock = AsyncMock(return_value=None)
        tracker_mock = _make_tracker_mock()

        with (
            patch.object(orch, "resolve_wait", new=resolve_mock),
            patch.object(orch, "_make_tracker", return_value=tracker_mock),
            patch.object(orch, "_do_probe", new=AsyncMock(return_value=True)),
            patch("roboco.events.get_event_bus") as mock_bus_fn,
        ):
            bus_mock = AsyncMock()
            bus_mock.publish = AsyncMock()
            mock_bus_fn.return_value = bus_mock

            await orch._probe_one_provider(provider, state)

        # Only the two anthropic-parked agents should be resolved
        assert resolve_mock.await_count == 2  # noqa: PLR2004
        resolved_ids = {call.args[0] for call in resolve_mock.call_args_list}
        assert agent1 in resolved_ids
        assert agent2 in resolved_ids
        assert "be-qa-1" not in resolved_ids

    async def test_rate_limit_lifted_event_published(self) -> None:
        """RATE_LIMIT_LIFTED event is published to the bus on probe success."""
        orch = _make_orchestrator()
        provider = "anthropic"
        state = _make_active_state(provider, retry_after=None)

        tracker_mock = _make_tracker_mock()
        published_events: list[Any] = []

        with (
            patch.object(orch, "resolve_wait", new=AsyncMock(return_value=None)),
            patch.object(orch, "_make_tracker", return_value=tracker_mock),
            patch.object(orch, "_do_probe", new=AsyncMock(return_value=True)),
            patch("roboco.events.get_event_bus") as mock_bus_fn,
        ):
            bus_mock = AsyncMock()
            bus_mock.publish = AsyncMock(side_effect=published_events.append)
            mock_bus_fn.return_value = bus_mock

            await orch._probe_one_provider(provider, state)

        assert len(published_events) == 1
        event = published_events[0]
        assert event.type == EventType.RATE_LIMIT_LIFTED
        assert event.data["provider"] == provider

    async def test_ceo_notified_flag_cleared_on_success(self) -> None:
        """_rate_limit_ceo_notified is cleared when probe succeeds."""
        orch = _make_orchestrator()
        provider = "anthropic"
        orch._rate_limit_ceo_notified.add(provider)  # simulates prior episode
        state = _make_active_state(provider, retry_after=None)

        tracker_mock = _make_tracker_mock()

        with (
            patch.object(orch, "resolve_wait", new=AsyncMock(return_value=None)),
            patch.object(orch, "_make_tracker", return_value=tracker_mock),
            patch.object(orch, "_do_probe", new=AsyncMock(return_value=True)),
            patch("roboco.events.get_event_bus") as mock_bus_fn,
        ):
            bus_mock = AsyncMock()
            bus_mock.publish = AsyncMock()
            mock_bus_fn.return_value = bus_mock

            await orch._probe_one_provider(provider, state)

        assert provider not in orch._rate_limit_ceo_notified

    async def test_probe_skipped_before_estimated_lift_at(self) -> None:
        """When retry_after has not elapsed yet the probe is skipped entirely."""
        orch = _make_orchestrator()
        provider = "anthropic"
        # Set activated_at to now; retry_after = 300s → estimated lift in future
        state = _make_active_state(
            provider,
            retry_after=300.0,
            activated_at=datetime.now(UTC),
        )

        probe_called: list[str] = []

        async def fake_do_probe(_p: str) -> bool:
            probe_called.append(_p)
            return True

        with patch.object(orch, "_do_probe", new=fake_do_probe):
            await orch._probe_one_provider(provider, state)

        assert probe_called == []  # probe was gated by time


# ---------------------------------------------------------------------------
# Tests: probe-failure path
# ---------------------------------------------------------------------------


class TestProbeFailurePath:
    """When _do_probe returns False the failure counter should be incremented."""

    async def test_increment_probe_failures_called_on_failure(self) -> None:
        """increment_probe_failures is called when the probe fails."""
        orch = _make_orchestrator()
        provider = "anthropic"
        state = _make_active_state(provider, retry_after=None)

        tracker_mock = _make_tracker_mock(failure_return=1)

        with (
            patch.object(orch, "_make_tracker", return_value=tracker_mock),
            patch.object(orch, "_do_probe", new=AsyncMock(return_value=False)),
            patch.object(orch, "_notify_rate_limit_ceo", new=AsyncMock()),
        ):
            await orch._probe_one_provider(provider, state)

        tracker_mock.increment_probe_failures.assert_awaited_once()

    async def test_clear_not_called_on_failure(self) -> None:
        """tracker.clear() must NOT be called when the probe fails."""
        orch = _make_orchestrator()
        provider = "anthropic"
        state = _make_active_state(provider, retry_after=None)

        tracker_mock = _make_tracker_mock(failure_return=1)

        with (
            patch.object(orch, "_make_tracker", return_value=tracker_mock),
            patch.object(orch, "_do_probe", new=AsyncMock(return_value=False)),
            patch.object(orch, "_notify_rate_limit_ceo", new=AsyncMock()),
        ):
            await orch._probe_one_provider(provider, state)

        tracker_mock.clear.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tests: CEO notification threshold
# ---------------------------------------------------------------------------


class TestCEONotificationThreshold:
    """CEO notification fires at count==10 exactly once per episode."""

    async def test_notification_fires_at_exactly_10_failures(self) -> None:
        """_notify_rate_limit_ceo is called when failure count hits 10."""
        orch = _make_orchestrator()
        provider = "anthropic"
        state = _make_active_state(provider, retry_after=None)

        # simulate already at 9 failures; next increment returns 10
        tracker_mock = _make_tracker_mock(failure_return=10)
        notify_mock = AsyncMock()

        with (
            patch.object(orch, "_make_tracker", return_value=tracker_mock),
            patch.object(orch, "_notify_rate_limit_ceo", new=notify_mock),
            patch.object(orch, "_do_probe", new=AsyncMock(return_value=False)),
        ):
            await orch._probe_one_provider(provider, state)

        notify_mock.assert_awaited_once()

    async def test_notification_not_fired_before_threshold(self) -> None:
        """No CEO notification below threshold 10."""
        orch = _make_orchestrator()
        provider = "anthropic"
        state = _make_active_state(provider, retry_after=None)

        tracker_mock = _make_tracker_mock(failure_return=9)
        notify_mock = AsyncMock()

        with (
            patch.object(orch, "_make_tracker", return_value=tracker_mock),
            patch.object(orch, "_notify_rate_limit_ceo", new=notify_mock),
            patch.object(orch, "_do_probe", new=AsyncMock(return_value=False)),
        ):
            await orch._probe_one_provider(provider, state)

        notify_mock.assert_not_awaited()

    async def test_notification_sent_only_once_per_episode(self) -> None:
        """Even if failures keep accumulating, the CEO is notified only once."""
        orch = _make_orchestrator()
        provider = "anthropic"
        state = _make_active_state(provider, retry_after=None)

        # Mark this episode as already notified
        orch._rate_limit_ceo_notified.add(provider)

        tracker_mock = _make_tracker_mock(failure_return=15)
        notify_mock = AsyncMock()

        with (
            patch.object(orch, "_make_tracker", return_value=tracker_mock),
            patch.object(orch, "_notify_rate_limit_ceo", new=notify_mock),
            patch.object(orch, "_do_probe", new=AsyncMock(return_value=False)),
        ):
            await orch._probe_one_provider(provider, state)

        notify_mock.assert_not_awaited()

    async def test_new_episode_allows_new_notification(self) -> None:
        """After a rate-limit clears (success) a new episode starts fresh."""
        orch = _make_orchestrator()
        provider = "anthropic"
        # Episode 1: had a notification
        orch._rate_limit_ceo_notified.add(provider)

        success_state = _make_active_state(provider, retry_after=None)
        tracker_mock = _make_tracker_mock(failure_return=10)
        notify_mock = AsyncMock()

        with (
            patch.object(orch, "resolve_wait", new=AsyncMock(return_value=None)),
            patch.object(orch, "_make_tracker", return_value=tracker_mock),
            patch.object(orch, "_notify_rate_limit_ceo", new=notify_mock),
            patch.object(orch, "_do_probe", new=AsyncMock(return_value=True)),
            patch("roboco.events.get_event_bus") as mock_bus_fn,
        ):
            bus_mock = AsyncMock()
            bus_mock.publish = AsyncMock()
            mock_bus_fn.return_value = bus_mock

            # Success clears the episode flag
            await orch._probe_one_provider(provider, success_state)

        assert provider not in orch._rate_limit_ceo_notified

        # Episode 2: simulate a new failure reaching threshold 10
        failure_state = _make_active_state(provider, retry_after=None)

        with (
            patch.object(orch, "_make_tracker", return_value=tracker_mock),
            patch.object(orch, "_notify_rate_limit_ceo", new=notify_mock),
            patch.object(orch, "_do_probe", new=AsyncMock(return_value=False)),
        ):
            await orch._probe_one_provider(provider, failure_state)

        # Notification SHOULD fire for the new episode
        notify_mock.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tests: orphan-provider fallback (F045)
# ---------------------------------------------------------------------------


class TestOrphanProviderFallback:
    """F045: an activate() failure in the in-verb ``i_am_blocked(rate_limited)``
    path leaves agents parked in ``_waiting_records`` but the provider never
    makes it into the tracker — so the tracker-driven loop never probes it and
    the parked agents strand in WAITING_LONG forever. The sweep must scan the
    in-memory records for any ``rate_limit_lifted`` provider the tracker-listed
    set did NOT cover and probe it via the time-expiry fallback so
    ``_on_probe_success`` can resume them.
    """

    async def test_orphan_parked_agent_resumed_when_tracker_lacks_provider(
        self,
    ) -> None:
        orch = _make_orchestrator()
        provider = "anthropic"
        agent = "be-dev-1"
        orch._waiting_records = {agent: _waiting_record(agent, provider)}

        tracker_mock = _make_tracker_mock()
        resolve_mock = AsyncMock(return_value=None)

        with (
            patch.object(orch, "resolve_wait", new=resolve_mock),
            patch.object(orch, "_make_tracker", return_value=tracker_mock),
            patch.object(orch, "_do_probe", new=AsyncMock(return_value=True)),
            patch.object(
                RateLimitStateTracker,
                "list_rate_limited_providers",
                new=AsyncMock(return_value=[]),
            ),
            patch("roboco.events.get_event_bus") as mock_bus_fn,
        ):
            bus_mock = AsyncMock()
            bus_mock.publish = AsyncMock()
            mock_bus_fn.return_value = bus_mock

            await orch._sweep_rate_limit_probes()

        # The orphan provider was probed and the parked agent resumed.
        assert resolve_mock.await_count == 1
        assert resolve_mock.call_args.args[0] == agent

    async def test_orphan_skipped_when_tracker_already_covers_provider(
        self,
    ) -> None:
        """A provider the tracker lists must NOT be double-probed via the fallback."""
        orch = _make_orchestrator()
        provider = "anthropic"
        agent = "be-dev-1"
        orch._waiting_records = {agent: _waiting_record(agent, provider)}

        tracker_mock = _make_tracker_mock()
        resolve_mock = AsyncMock(return_value=None)
        state = _make_active_state(provider, retry_after=None)

        probe_calls: list[str] = []

        async def fake_do_probe(p: str) -> bool:
            probe_calls.append(p)
            return True

        with (
            patch.object(orch, "resolve_wait", new=resolve_mock),
            patch.object(orch, "_make_tracker", return_value=tracker_mock),
            patch.object(orch, "_do_probe", new=fake_do_probe),
            patch.object(
                RateLimitStateTracker,
                "list_rate_limited_providers",
                new=AsyncMock(return_value=[(provider, state)]),
            ),
            patch("roboco.events.get_event_bus") as mock_bus_fn,
        ):
            bus_mock = AsyncMock()
            bus_mock.publish = AsyncMock()
            mock_bus_fn.return_value = bus_mock

            await orch._sweep_rate_limit_probes()

        # Probed exactly once (via the tracker-listed path), not twice.
        assert probe_calls == [provider]


# ---------------------------------------------------------------------------
# Tests: list_rate_limited_providers
# ---------------------------------------------------------------------------


class TestListRateLimitedProviders:
    """list_rate_limited_providers scans Redis for active rate-limit keys."""

    async def test_returns_empty_when_no_keys(self) -> None:
        redis_mock = _make_redis_mock()
        with patch("redis.asyncio.from_url", return_value=redis_mock):
            result = await RateLimitStateTracker.list_rate_limited_providers()
        assert result == []

    async def test_returns_active_provider(self) -> None:
        state = {
            "rate_limited": True,
            "activated_at": datetime.now(UTC).isoformat(),
            "retry_after": 60.0,
            "affected_agents": ["be-dev-1"],
            "probe_failures": 0,
        }
        store = {"roboco:rate_limit:anthropic:state": json.dumps(state).encode()}
        redis_mock = _make_redis_mock(store)

        with patch("redis.asyncio.from_url", return_value=redis_mock):
            result = await RateLimitStateTracker.list_rate_limited_providers()

        assert len(result) == 1
        provider, returned_state = result[0]
        assert provider == "anthropic"
        assert returned_state["rate_limited"] is True

    async def test_ignores_cleared_providers(self) -> None:
        state = {
            "rate_limited": False,
            "activated_at": datetime.now(UTC).isoformat(),
            "retry_after": 60.0,
            "affected_agents": [],
            "probe_failures": 2,
        }
        store = {"roboco:rate_limit:anthropic:state": json.dumps(state).encode()}
        redis_mock = _make_redis_mock(store)

        with patch("redis.asyncio.from_url", return_value=redis_mock):
            result = await RateLimitStateTracker.list_rate_limited_providers()

        assert result == []


# ---------------------------------------------------------------------------
# Tests: GET /api/system/rate-limits endpoint schema
# ---------------------------------------------------------------------------


class TestRateLimitsEndpoint:
    """GET /api/system/rate-limits returns correct schema."""

    async def test_returns_empty_list_when_no_rate_limits(self) -> None:
        app = create_app()

        with patch(
            "roboco.api.routes.system.RateLimitStateTracker"
            ".list_rate_limited_providers",
            new_callable=AsyncMock,
            return_value=[],
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/system/rate-limits")

        assert resp.status_code == _HTTP_OK
        assert resp.json() == {"entries": []}

    async def test_returns_provider_state_when_rate_limited(self) -> None:
        app = create_app()

        retry_after = 60.0
        state = {
            "rate_limited": True,
            "activated_at": "2026-06-11T00:00:00+00:00",
            "retry_after": retry_after,
            "affected_agents": ["be-dev-1"],
            "probe_failures": 3,
        }

        with patch(
            "roboco.api.routes.system.RateLimitStateTracker"
            ".list_rate_limited_providers",
            new_callable=AsyncMock,
            return_value=[("anthropic", state)],
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/system/rate-limits")

        assert resp.status_code == _HTTP_OK
        entries = resp.json()["entries"]
        assert len(entries) == 1
        entry = entries[0]
        # Panel-shaped, camelCase fields (not the raw Redis state).
        assert entry["provider"] == "anthropic"
        assert entry["affectedAgents"] == ["be-dev-1"]
        assert entry["hitAt"] == "2026-06-11T00:00:00+00:00"
        assert entry["retryAfterSeconds"] == retry_after
        assert entry["resumeAt"] == "2026-06-11T00:01:00+00:00"

    async def test_endpoint_not_404(self) -> None:
        """The endpoint must be registered in app.py — no 404."""
        app = create_app()

        with patch(
            "roboco.api.routes.system.RateLimitStateTracker"
            ".list_rate_limited_providers",
            new_callable=AsyncMock,
            return_value=[],
        ):
            async with AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as client:
                resp = await client.get("/api/system/rate-limits")

        assert resp.status_code != _HTTP_NOT_FOUND
