"""Cross-task review-path outage detection."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException, status
from fastapi.routing import APIRoute
from roboco.api.routes import health as health_routes
from roboco.api.routes.health import review_path_health
from roboco.api.utils.git import _translate_read_error
from roboco.config import settings
from roboco.exceptions import GitTimeoutError
from roboco.services.base import NotFoundError, ServiceError
from roboco.services.gateway.choreographer.evidence_legs import (
    LegBudget,
    run_bounded_leg,
)
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


@pytest.fixture
def tracker() -> Iterator[ReviewPathHealthTracker]:
    t = ReviewPathHealthTracker()
    with patch("roboco.services.review_path_health._tracker", t):
        yield t


def _failed_keys(t: ReviewPathHealthTracker) -> list[str]:
    return [key for _, key in t._failures]


async def _boom(exc: BaseException) -> None:
    raise exc


async def _ok() -> str:
    return "done"


@pytest.mark.asyncio
async def test_sweep_reports_outage_that_tripped_and_cleared_between_sweeps(
    tracker: ReviewPathHealthTracker,
) -> None:
    for key in "abc":
        tracker.record_failure(key)
    tracker.record_success()
    tracker.record_success()
    send = AsyncMock()
    with patch(
        "roboco.services.notification.NotificationService."
        "send_review_path_outage_notification",
        send,
    ):
        await sweep_review_path_health()
        await sweep_review_path_health()
    assert [c.kwargs["active"] for c in send.await_args_list] == [True, False]


@pytest.mark.asyncio
async def test_run_bounded_leg_records_failure_keyed_on_task_id(
    tracker: ReviewPathHealthTracker,
) -> None:
    gaps: list[str] = []
    for task_id in ("t1", "t2", "t3"):
        await run_bounded_leg(
            _boom(GitTimeoutError("git log", 5)),
            default=None,
            budget=LegBudget(30),
            leg="log",
            hint="h",
            task_id=task_id,
            gaps=gaps,
        )
    assert _failed_keys(tracker) == ["t1", "t2", "t3"]
    assert tracker.state()["outage_active"] is True


@pytest.mark.asyncio
async def test_run_bounded_leg_records_unexpected_error_and_reraises(
    tracker: ReviewPathHealthTracker,
) -> None:
    with pytest.raises(RuntimeError):
        await run_bounded_leg(
            _boom(RuntimeError("x")),
            default=None,
            budget=LegBudget(30),
            leg="log",
            hint="h",
            task_id="t9",
            gaps=[],
        )
    assert _failed_keys(tracker) == ["t9"]


@pytest.mark.asyncio
async def test_run_bounded_leg_success_counts_toward_recovery(
    tracker: ReviewPathHealthTracker,
) -> None:
    for key in "abc":
        tracker.record_failure(key)
    for _ in range(settings.review_path_recovery_successes):
        result = await run_bounded_leg(
            _ok(),
            default="",
            budget=LegBudget(30),
            leg="log",
            hint="h",
            task_id="t1",
            gaps=[],
        )
        assert result == "done"
    assert tracker.state()["outage_active"] is False
    assert tracker.state()["recovered"] is True


def test_translate_read_error_records_5xx_with_given_key(
    tracker: ReviewPathHealthTracker,
) -> None:
    exc = _translate_read_error(ServiceError("boom"), "feature/backend/abc")
    assert isinstance(exc, HTTPException)
    assert exc.status_code == status.HTTP_500_INTERNAL_SERVER_ERROR
    assert _failed_keys(tracker) == ["feature/backend/abc"]


def test_translate_read_error_ignores_4xx(
    tracker: ReviewPathHealthTracker,
) -> None:
    exc = _translate_read_error(NotFoundError("Branch", "x"), "k")
    assert exc.status_code == status.HTTP_404_NOT_FOUND
    assert _failed_keys(tracker) == []


@pytest.mark.asyncio
async def test_health_route_returns_contract_fields(
    tracker: ReviewPathHealthTracker,
) -> None:
    idle = await review_path_health()
    assert idle.model_dump() == {
        "outage_active": False,
        "type": "review_path_tools",
        "window_start": None,
        "recovered": False,
    }
    for key in "abc":
        tracker.record_failure(key)
    active = await review_path_health()
    assert active.outage_active is True
    assert active.window_start is not None
    assert active.type == "review_path_tools"


def test_health_route_serves_frozen_path() -> None:
    routes = health_routes.router.routes
    paths = {r.path for r in routes if isinstance(r, APIRoute)}
    assert "/api/system-health/review-path" in paths
