"""Unit tests for the Decisions spend guard (spec 3.1): the twice-a-402-in-
an-hour alert, the cumulative daily fallback-spend threshold, and the
fail-open notification posture."""

from unittest.mock import AsyncMock

import pytest
import roboco.config as cfg
from roboco.services.decisions import spend_guard


@pytest.fixture(autouse=True)
def _reset_spend_state():
    spend_guard.reset_spend_guard()
    yield
    spend_guard.reset_spend_guard()


@pytest.mark.asyncio
async def test_two_402s_within_an_hour_fire_exactly_one_notification(
    monkeypatch,
):
    alert = AsyncMock()
    monkeypatch.setattr(spend_guard, "_send_ceo_alert", alert)

    spend_guard.record_402("openrouter")
    await spend_guard.drain_pending_alerts()
    alert.assert_not_awaited()

    spend_guard.record_402("openrouter")
    await spend_guard.drain_pending_alerts()
    assert alert.await_count == 1


@pytest.mark.asyncio
async def test_402_counter_resets_after_alerting(monkeypatch):
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
async def test_single_402_then_one_402_after_reset_window(monkeypatch):
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
async def test_402_alerts_are_per_tier(monkeypatch):
    alert = AsyncMock()
    monkeypatch.setattr(spend_guard, "_send_ceo_alert", alert)

    spend_guard.record_402("laya")
    spend_guard.record_402("openrouter")
    await spend_guard.drain_pending_alerts()
    alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_spend_threshold_crossing_fires_one_notification(monkeypatch):
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
async def test_second_threshold_notification_suppressed_same_day(monkeypatch):
    monkeypatch.setattr(cfg.settings, "decisions_cost_alert_usd", 0.1)
    alert = AsyncMock()
    monkeypatch.setattr(spend_guard, "_send_ceo_alert", alert)

    spend_guard.record_spend("openrouter", 0.2)
    spend_guard.record_spend("openrouter", 5.0)
    spend_guard.record_spend("openrouter", 5.0)
    await spend_guard.drain_pending_alerts()
    assert alert.await_count == 1


@pytest.mark.asyncio
async def test_threshold_state_tracks_per_day(monkeypatch):
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
async def test_laya_tier_never_alerts(monkeypatch):
    monkeypatch.setattr(cfg.settings, "decisions_cost_alert_usd", 0.01)
    alert = AsyncMock()
    monkeypatch.setattr(spend_guard, "_send_ceo_alert", alert)

    spend_guard.record_spend("laya", 0.0)
    spend_guard.record_spend("laya", 1000.0)  # even a bogus nonzero cost
    await spend_guard.drain_pending_alerts()
    alert.assert_not_awaited()


@pytest.mark.asyncio
async def test_none_cost_recorded_as_zero(monkeypatch):
    alert = AsyncMock()
    monkeypatch.setattr(spend_guard, "_send_ceo_alert", alert)

    spend_guard.record_spend("openrouter", None)
    await spend_guard.drain_pending_alerts()
    alert.assert_not_awaited()
    day = next(iter(spend_guard._STATE["spend_by_day"]["openrouter"]))
    assert spend_guard._STATE["spend_by_day"]["openrouter"][day] == 0.0


@pytest.mark.asyncio
async def test_notification_failure_swallowed(monkeypatch):
    """A notification failure must never break the decision flow (spec 3.1):
    _send_ceo_alert swallows everything."""
    import roboco.services.notification as notification_module

    class _BoomNotificationService:
        def __init__(self, session):
            pass

        async def _create_notification(self, params):
            raise RuntimeError("db down")

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(
        notification_module, "NotificationService", _BoomNotificationService
    )
    monkeypatch.setattr("roboco.db.base.get_session_factory", lambda: _FakeSession)
    await spend_guard._send_ceo_alert("subject", "body")  # must not raise


@pytest.mark.asyncio
async def test_alert_is_ceo_facing_ack_required(monkeypatch):
    captured = {}

    class _FakeNotificationService:
        def __init__(self, session):
            pass

        async def _create_notification(self, params):
            captured["params"] = params

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
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
