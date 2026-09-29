"""The task-scoped wedge ledger.

The #685 oscillation breaker counts strikes per unblock only, and the
September mixed-verb loop routed around it: unblock succeeds → the next
verb bounces ``not_authorized`` → an escalation blocks the task → the pool
reclaims it → respawn — with no single verb accumulating strikes. The
wedge ledger counts re-arrivals at a STATUS (any actor, any verb) while the
#685 progress fingerprint is static, resets on any of the three signals
moving, and force-blocks for a human past the threshold with the full
actor/transition/timestamp cycle in the CEO notification.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.foundation.policy.content import markers
from roboco.models import NotificationType
from roboco.services.notification import NotificationService
from roboco.services.wedge_ledger import (
    WedgeLedgerService,
    evaluate_arrival,
    get_wedge_state,
    max_strikes,
    progress_fingerprint,
)

# Mirrors Settings.wedge_ledger_threshold's default — ruff PLR2004 forbids
# magic-value comparisons.
DEFAULT_THRESHOLD = 5
_TWO_LEGS = 2
_THREE_ARRIVALS = 3
_FOUR_ARRIVALS = 4
_MIN_COMMITS_FOR_TRIP = 2


def _fp() -> list[int]:
    """A static fingerprint: no commits, no revisions, no terminal children —
    exactly the wedged-loop shape."""
    return progress_fingerprint(0, 0, 0)


def _drive(ledger: dict[str, Any], arrivals: list[str]) -> dict[str, Any]:
    for status in arrivals:
        ledger = evaluate_arrival(ledger, status, _fp())
    return ledger


# --- pure evaluator ---------------------------------------------------------


def test_first_arrival_records_fingerprint_and_accrues_like_685() -> None:
    ledger = evaluate_arrival({}, "in_progress", _fp())
    assert ledger["progress_fp"] == _fp()
    # The #685 counter accrues on the first bump too — same semantics here.
    assert ledger["strikes"] == {"in_progress": 1}
    assert max_strikes(ledger) == 1


def test_return_to_status_with_no_movement_accrues_a_strike() -> None:
    ledger = _drive({}, ["in_progress", "blocked", "in_progress"])
    assert ledger["strikes"]["in_progress"] == _TWO_LEGS


def test_reset_rule_any_fingerprint_signal_moving_clears_all_counters() -> None:
    ledger = _drive({}, ["in_progress"] * _FOUR_ARRIVALS)
    assert max_strikes(ledger) == _FOUR_ARRIVALS
    # One commit lands (first fingerprint signal moves)…
    ledger = evaluate_arrival(ledger, "blocked", progress_fingerprint(1, 0, 0))
    assert ledger["strikes"] == {}
    # …a revision bounce moves the second signal…
    ledger = _drive(ledger, ["blocked"])
    ledger = evaluate_arrival(ledger, "blocked", progress_fingerprint(1, 1, 0))
    assert ledger["strikes"] == {}
    # …and a terminal child moves the third.
    ledger = _drive(ledger, ["blocked"] * 3)
    ledger = evaluate_arrival(ledger, "blocked", progress_fingerprint(1, 1, 1))
    assert ledger["strikes"] == {}


def test_threshold_default_is_aligned_with_the_685_trip_threshold() -> None:
    assert settings.wedge_ledger_threshold == DEFAULT_THRESHOLD


# --- the September mixed-verb loop -------------------------------------------


def test_september_mixed_verb_loop_accrues_strikes_and_trips() -> None:
    """The exact shape the per-unblock counter missed: one full cycle is
    unblock succeeds (blocked→in_progress) → next verb not_authorized (NO
    transition) → escalate bounce (in_progress→blocked) → pool reclaim
    (blocked→pending) → claim → start (…→in_progress). Actors alternate
    (main-pm unblocks, cell-pm escalates, system reclaims) and no single
    verb repeats — under the per-unblock counter this loop accrued ~1
    strike per full cycle; status-keyed, every leg of the loop counts."""
    cycle = [
        "in_progress",
        "blocked",
        "in_progress",
        "pending",
        "claimed",
        "in_progress",
    ]
    ledger: dict[str, Any] = {}
    for _ in range(DEFAULT_THRESHOLD):
        ledger = _drive(ledger, cycle)
    # The loop kept returning to in_progress with zero fingerprint movement.
    assert ledger["strikes"]["in_progress"] >= DEFAULT_THRESHOLD
    assert max_strikes(ledger) > DEFAULT_THRESHOLD

    # Contrast: the #685 per-unblock counter only ever saw the ONE unblock
    # restore per cycle (the not_authorized verb and the pool reclaim bumped
    # nothing), so it needed 6 unblocks — 6 full cycles — to trip, while the
    # status-keyed ledger tripped in 3 (in_progress is re-entered twice per
    # cycle). The loop's other legs were invisible to it.
    t = MagicMock(orchestration_markers=None)
    per_unblock = 0
    for _ in range(DEFAULT_THRESHOLD):
        per_unblock = markers.bump_oscillation_strikes(t, _fp())
    assert per_unblock == DEFAULT_THRESHOLD  # not yet tripped (> threshold required)


# --- service-level behavior (mocked session) ---------------------------------


def _mock_task() -> MagicMock:
    return MagicMock(
        id=uuid4(),
        commits=[],
        revision_count=0,
        title="wedged task",
        orchestration_markers=None,
        blocker_resolver_type=None,
    )


def _mock_session(task: MagicMock | None) -> MagicMock:
    session = MagicMock()
    session.get = AsyncMock(return_value=task)
    session.commit = AsyncMock()
    # The trip's cycle query reads the audit log; an empty result keeps the
    # mock session honest without a real DB.
    empty = MagicMock(
        scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=[])))
    )
    session.execute = AsyncMock(return_value=empty)
    return session


@pytest.fixture()
def _no_side_effects(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Stub the child COUNT, the force-block transition, and the CEO
    notification so WedgeLedgerService runs against a mocked session."""
    calls: dict[str, Any] = {"admin": AsyncMock(), "notify": AsyncMock()}
    monkeypatch.setattr(
        "roboco.services.wedge_ledger._terminal_children_count",
        AsyncMock(return_value=0),
    )
    monkeypatch.setattr(
        "roboco.services.task.TaskService.admin_set_status", calls["admin"]
    )
    monkeypatch.setattr(
        "roboco.services.notification.NotificationService."
        "send_wedge_blocked_notification",
        calls["notify"],
    )
    return calls


@pytest.mark.usefixtures("_no_side_effects")
async def test_check_transition_skips_blocked_and_terminal_arrivals() -> None:
    task = _mock_task()
    service = WedgeLedgerService(_mock_session(task))
    for status in ("blocked", "completed", "cancelled"):
        assert await service.check_transition(task.id, status) is None
    # Skipped arrivals never loaded or wrote anything.
    service.session.get.assert_not_awaited()


@pytest.mark.usefixtures("_no_side_effects")
async def test_check_transition_trips_blocks_and_carries_the_cycle() -> None:
    task = _mock_task()
    session = _mock_session(task)
    service = WedgeLedgerService(session)
    trip: dict[str, Any] | None = None
    for _ in range(DEFAULT_THRESHOLD + 1):
        trip = await service.check_transition(task.id, "in_progress")
    assert trip is not None
    assert trip["strikes"] == DEFAULT_THRESHOLD + 1
    assert task.blocker_resolver_type == "human"
    assert session.commit.await_count >= _MIN_COMMITS_FOR_TRIP
    # The CEO payload names every loop leg, not just the trip count.
    cycle = trip["cycle"]
    assert all(
        {"timestamp", "actor", "actor_role", "from_status", "to_status"} <= set(entry)
        for entry in cycle
    )


@pytest.mark.usefixtures("_no_side_effects")
async def test_lower_threshold_trips_sooner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "wedge_ledger_threshold", 2)
    task = _mock_task()
    service = WedgeLedgerService(_mock_session(task))
    trip = None
    for _ in range(3):
        trip = await service.check_transition(task.id, "pending")
    assert trip is not None
    assert trip["strikes"] == _THREE_ARRIVALS


async def test_missing_task_is_a_noop() -> None:
    service = WedgeLedgerService(_mock_session(None))
    assert await service.check_transition(uuid4(), "pending") is None


def test_get_wedge_state_fills_defaults_for_an_unmarked_task() -> None:
    state = get_wedge_state(MagicMock(orchestration_markers=None))
    assert state == {
        "progress_fp": [],
        "strikes": {},
        "tripped": False,
        "tripped_at": None,
        "tripped_strikes": 0,
    }


# --- CEO notification ---------------------------------------------------------


async def test_wedge_blocked_notification_carries_the_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[Any] = []
    monkeypatch.setattr(
        NotificationService,
        "_create_notification",
        AsyncMock(side_effect=lambda params, **_: created.append(params) or None),
    )
    cycle = [
        {
            "timestamp": "2026-09-05T10:00:00+00:00",
            "event": "task.in_progress",
            "actor": "11111111-1111-1111-1111-111111111111",
            "actor_role": "main_pm",
            "from_status": "blocked",
            "to_status": "in_progress",
        },
        {
            "timestamp": "2026-09-05T10:05:00+00:00",
            "event": "task.blocked",
            "actor": "22222222-2222-2222-2222-222222222222",
            "actor_role": "cell_pm",
            "from_status": "in_progress",
            "to_status": "blocked",
        },
    ]
    await NotificationService().send_wedge_blocked_notification(
        task_id=str(uuid4()),
        strikes=6,
        status="in_progress",
        cycle=cycle,
        task_title="wedged task",
    )
    assert len(created) == 1
    params = created[0]
    assert params.notification_type == NotificationType.BLOCKER_ESCALATION
    assert params.to_agents == ["ceo"]
    # Every loop leg is named: actor, role, transition, timestamp.
    for entry in cycle:
        assert entry["timestamp"] in params.body
        assert entry["actor"] in params.body
        assert entry["actor_role"] in params.body
        assert f"{entry['from_status']} -> {entry['to_status']}" in params.body
