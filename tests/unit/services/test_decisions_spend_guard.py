"""Unit tests for the Decisions spend guard (spec 3.1): the twice-a-402-in-
an-hour alert, the cumulative daily fallback-spend threshold, and the
fail-open notification posture."""

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock

import pytest
import roboco.config as cfg
from roboco.services.decisions import spend_guard


@pytest.fixture(autouse=True)
def _reset_spend_state() -> Iterator[None]:
    spend_guard.reset_spend_guard()
    yield
    spend_guard.reset_spend_guard()


@pytest.mark.asyncio
async def test_two_402s_within_an_hour_fire_exactly_one_notification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alert = AsyncMock()
    monkeypatch.setattr(spend_guard, "_send_ceo_alert", alert)

    spend_guard.record_402("openrouter")
    await spend_guard.drain_pending_alerts()
    alert.assert_not_awaited()

    spend_guard.record_402("openrouter")
    await spend_guard.drain_pending_alerts()
    assert alert.await_count == 1


@pytest.mark.asyncio
async def test_402_counter_resets_after_alerting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alert = AsyncMock()
    monkeypatch.setattr(spend_guard, "_send_ceo_alert", alert)

    spend_guard.record_402("openrouter")
    spend_guard.record_402("openrouter")
    await spend_guard.drain_pending_alerts()
    assert alert.await_count == 1

    # The counter reset: two more 402s fire again, exactly once more.
    spend_guard.record_402("openrouter")
    spend_guard.record_402("openrouter")
    await spend_guard.drain_pending_alerts()
    assert alert.await_count == 2


@pytest.mark.asyncio
async def test_single_402_then_one_402_after_reset_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single 402 never alerts; the counter decays, so one 402 now plus
    one 402 past the window (simulated by clearing state) is still silent."""
    alert = AsyncMock()
    monkeypatch.setattr(spend_guard, "_send_ceo_alert", alert)

    spend_guard.record_402("openrouter")
    # Simulate the rolling hour expiring for the first stamp.
    spend_guard._STATE["402_timestamps"]["openrouter"] = [
        stamp - 7200 for stamp in spend_guard._STATE["402_timestamps"]["openrouter"]
    ]
    spend_guard.record_402("openrouter")
    await spend_guard.drain_pending_alerts()
    alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_402_alerts_are_per_tier(monkeypatch: pytest.MonkeyPatch) -> None:
    alert = AsyncMock()
    monkeypatch.setattr(spend_guard, "_send_ceo_alert", alert)

    spend_guard.record_402("laya")
    spend_guard.record_402("openrouter")
    await spend_guard.drain_pending_alerts()
    alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_spend_threshold_crossing_fires_one_notification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg.settings, "decisions_cost_alert_usd", 0.5)
    alert = AsyncMock()
    monkeypatch.setattr(spend_guard, "_send_ceo_alert", alert)

    spend_guard.record_spend("openrouter", 0.3)
    await spend_guard.drain_pending_alerts()
    alert.assert_not_awaited()  # below threshold: silent

    spend_guard.record_spend("openrouter", 0.3)
    await spend_guard.drain_pending_alerts()
    assert alert.await_count == 1  # crossed: one notification


@pytest.mark.asyncio
async def test_second_threshold_notification_suppressed_same_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg.settings, "decisions_cost_alert_usd", 0.1)
    alert = AsyncMock()
    monkeypatch.setattr(spend_guard, "_send_ceo_alert", alert)

    spend_guard.record_spend("openrouter", 0.2)
    spend_guard.record_spend("openrouter", 5.0)
    spend_guard.record_spend("openrouter", 5.0)
    await spend_guard.drain_pending_alerts()
    assert alert.await_count == 1


@pytest.mark.asyncio
async def test_threshold_state_tracks_per_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """A new UTC day re-arms the alert (simulated by moving the marker)."""
    monkeypatch.setattr(cfg.settings, "decisions_cost_alert_usd", 0.1)
    alert = AsyncMock()
    monkeypatch.setattr(spend_guard, "_send_ceo_alert", alert)

    spend_guard.record_spend("openrouter", 0.2)
    await spend_guard.drain_pending_alerts()
    assert alert.await_count == 1

    spend_guard._STATE["spend_alerted_day"]["openrouter"] = "2000-01-01"
    spend_guard.record_spend("openrouter", 0.2)
    await spend_guard.drain_pending_alerts()
    assert alert.await_count == 2


@pytest.mark.asyncio
async def test_laya_tier_never_alerts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg.settings, "decisions_cost_alert_usd", 0.01)
    alert = AsyncMock()
    monkeypatch.setattr(spend_guard, "_send_ceo_alert", alert)

    spend_guard.record_spend("laya", 0.0)
    spend_guard.record_spend("laya", 1000.0)  # even a bogus nonzero cost
    await spend_guard.drain_pending_alerts()
    alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_none_cost_recorded_as_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    alert = AsyncMock()
    monkeypatch.setattr(spend_guard, "_send_ceo_alert", alert)

    spend_guard.record_spend("openrouter", None)
    await spend_guard.drain_pending_alerts()
    alert.assert_not_awaited()
    day = next(iter(spend_guard._STATE["spend_by_day"]["openrouter"]))
    assert spend_guard._STATE["spend_by_day"]["openrouter"][day] == 0.0


@pytest.mark.asyncio
async def test_notification_failure_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """A notification failure must never break the decision flow (spec 3.1):
    _send_ceo_alert swallows everything."""
    import roboco.services.notification as notification_module

    class _BoomNotificationService:
        def __init__(self) -> None:
            pass

        async def _create_notification(self, params: object, **kwargs: object) -> None:
            raise RuntimeError("db down")

    class _FakeSession:
        async def __aenter__(self) -> "_FakeSession":
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

    monkeypatch.setattr(
        notification_module, "NotificationService", _BoomNotificationService
    )
    monkeypatch.setattr("roboco.db.base.get_session_factory", lambda: _FakeSession)
    await spend_guard._send_ceo_alert("subject", "body")  # must not raise


@pytest.mark.asyncio
async def test_alert_is_ceo_facing_ack_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = {}

    class _FakeNotificationService:
        def __init__(self) -> None:
            pass

        async def _create_notification(self, params: Any, **kwargs: object) -> None:
            captured["params"] = params

    class _FakeSession:
        async def __aenter__(self) -> "_FakeSession":
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

    import roboco.services.notification as notification_module

    monkeypatch.setattr(
        notification_module, "NotificationService", _FakeNotificationService
    )
    monkeypatch.setattr("roboco.db.base.get_session_factory", lambda: _FakeSession)
    await spend_guard._send_ceo_alert("subject", "body")
    params = captured["params"]
    assert params.requires_ack is True
    assert params.bypass_purpose_dedup is True
    assert params.to_agents == ["ceo"]
