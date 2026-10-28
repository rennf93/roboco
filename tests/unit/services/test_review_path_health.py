"""Cross-task review-path outage detection."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from roboco.config import settings
from roboco.services.review_path_health import (
    ReviewPathHealthTracker,
    sweep_review_path_health,
)


@pytest.fixture
def t0() -> datetime:
    return datetime.now(UTC) - timedelta(seconds=30)


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "review_path_outage_window_seconds", 300)
    monkeypatch.setattr(settings, "review_path_outage_distinct_tasks", 3)
    monkeypatch.setattr(settings, "review_path_recovery_successes", 2)


def test_single_task_failing_repeatedly_is_not_an_outage(t0: datetime) -> None:
    t = ReviewPathHealthTracker()
    for i in range(20):
        t.record_failure("task-a", t0 + timedelta(seconds=i))
    assert t.state(t0 + timedelta(seconds=21))["outage_active"] is False
    assert t.pending_transition() is None


def test_distinct_tasks_trip_once_and_dedupe(t0: datetime) -> None:
    t = ReviewPathHealthTracker()
    for i, key in enumerate(["a", "b", "c", "a", "d"]):
        t.record_failure(key, t0 + timedelta(seconds=i))
    state = t.state(t0 + timedelta(seconds=10))
    assert state == {
        "outage_active": True,
        "type": "review_path_tools",
        "window_start": t0,
        "recovered": False,
    }
    assert t.pending_transition() == "active"
    assert t.pending_transition() is None


def test_failures_outside_window_do_not_count(t0: datetime) -> None:
    t = ReviewPathHealthTracker()
    t.record_failure("a", t0)
    t.record_failure("b", t0 + timedelta(seconds=1))
    t.record_failure("c", t0 + timedelta(seconds=400))
    assert t.state(t0 + timedelta(seconds=401))["outage_active"] is False


def test_consecutive_successes_recover(t0: datetime) -> None:
    t = ReviewPathHealthTracker()
    for i, key in enumerate("abc"):
        t.record_failure(key, t0 + timedelta(seconds=i))
    assert t.pending_transition() == "active"
    t.record_success()
    assert t.state()["outage_active"] is True
    t.record_success()
    state = t.state()
    assert state["outage_active"] is False
    assert state["recovered"] is True
    assert t.pending_transition() == "recovered"
    assert t.pending_transition() is None


@pytest.mark.asyncio
async def test_sweep_sends_one_notification_per_transition() -> None:
    t = ReviewPathHealthTracker()
    now = datetime.now(UTC)
    for key in "abc":
        t.record_failure(key, now)
    send = AsyncMock()
    with (
        patch("roboco.services.review_path_health._tracker", t),
        patch(
            "roboco.services.notification.NotificationService."
            "send_review_path_outage_notification",
            send,
        ),
    ):
        await sweep_review_path_health()
        await sweep_review_path_health()
    assert send.await_count == 1
    assert send.await_args is not None
    assert send.await_args.kwargs["active"] is True
