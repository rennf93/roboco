"""Tests for the task-trajectory outcome labeler (spec 12.1 Wave 1).

The fate rules are pure functions: exactly one observed fate labels a
row, conflicting or absent fates leave it unlabeled, and a bounced score
row gets the soft low gold rather than a fabricated pole."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
import roboco.config as cfg
from roboco.services.decisions import trajectory
from roboco.services.decisions.outcomes import (
    IDLE_WAIT_RESOLVED_AFTER,
    OWNED_TASK_ROTTED_AFTER,
    OWNED_TASK_SUBMITTED_AFTER,
    PLAN_BOUNCED_AFTER,
    QA_PASSED_AFTER,
    TASK_CANCELLED_AFTER,
    TASK_DELIVERED_AFTER,
    TASK_STALLED_PAST_WINDOW,
)
from roboco.services.decisions.trajectory import _FateSlugs, single_subject_slug

_T0 = datetime(2026, 6, 17, tzinfo=UTC)
_ROT = _T0 + timedelta(days=7)


def _delivered_at(ts: datetime) -> tuple[str, datetime]:
    return ("awaiting_qa", ts)


def test_delivered_after_decision_yields_delivered_slug() -> None:
    slugs = _FateSlugs(
        delivered=TASK_DELIVERED_AFTER,
        stalled=TASK_STALLED_PAST_WINDOW,
    )
    slug = single_subject_slug(
        ("awaiting_qa", _T0 + timedelta(hours=3)), _T0, _ROT, _ROT, slugs
    )
    assert slug == TASK_DELIVERED_AFTER


def test_cancelled_after_decision_yields_cancelled_slug() -> None:
    slugs = _FateSlugs(
        delivered=TASK_DELIVERED_AFTER,
        cancelled=TASK_CANCELLED_AFTER,
        stalled=TASK_STALLED_PAST_WINDOW,
    )
    slug = single_subject_slug(
        ("cancelled", _T0 + timedelta(hours=5)), _T0, _ROT, _ROT, slugs
    )
    assert slug == TASK_CANCELLED_AFTER


def test_stalled_past_rot_yields_stalled_slug() -> None:
    slugs = _FateSlugs(
        delivered=TASK_DELIVERED_AFTER,
        stalled=TASK_STALLED_PAST_WINDOW,
    )
    slug = single_subject_slug(
        ("in_progress", _T0 - timedelta(hours=1)),
        _T0,
        _ROT + timedelta(hours=1),
        _ROT,
        slugs,
    )
    assert slug == TASK_STALLED_PAST_WINDOW


def test_stalled_inside_window_is_unlabeled() -> None:
    slugs = _FateSlugs(
        delivered=TASK_DELIVERED_AFTER,
        stalled=TASK_STALLED_PAST_WINDOW,
    )
    slug = single_subject_slug(
        ("in_progress", _T0 - timedelta(hours=1)),
        _T0,
        _T0 + timedelta(hours=2),
        _ROT,
        slugs,
    )
    assert slug is None


def test_bounced_yields_soft_bounce_slug_only_when_rule_has_one() -> None:
    bounce_at = _T0 + timedelta(hours=6)
    slug = single_subject_slug(
        ("needs_revision", bounce_at),
        _T0,
        _ROT,
        _ROT,
        _FateSlugs(delivered=QA_PASSED_AFTER, bounced=PLAN_BOUNCED_AFTER),
    )
    assert slug == PLAN_BOUNCED_AFTER
    # A pilot without a bounce gold (respawn) leaves bounced rows alone.
    slug = single_subject_slug(
        ("needs_revision", bounce_at),
        _T0,
        _ROT,
        _ROT,
        _FateSlugs(
            delivered=TASK_DELIVERED_AFTER,
            stalled=TASK_STALLED_PAST_WINDOW,
        ),
    )
    assert slug is None


def test_pre_gate_movement_is_not_evidence() -> None:
    # The task's last change happened BEFORE the decision: the delivered
    # status is stale evidence, not a post-decision fate.
    slug = single_subject_slug(
        ("awaiting_qa", _T0 - timedelta(hours=1)),
        _T0,
        _ROT,
        _ROT,
        _FateSlugs(
            delivered=TASK_DELIVERED_AFTER,
            stalled=TASK_STALLED_PAST_WINDOW,
        ),
    )
    assert slug is None


def _owned(task_id: str, status: str) -> dict[str, str]:
    return {"task_id": task_id, "status": status}


def test_idle_unsubmitted_then_submitted_proves_likely_done() -> None:
    slug = trajectory.idle_legitimacy_slug(
        [_owned("t1", "in_progress")],
        {"t1": _delivered_at(_T0 + timedelta(hours=2))},
        _T0,
        _ROT,
        _ROT,
    )
    assert slug == OWNED_TASK_SUBMITTED_AFTER


def test_idle_waiting_then_progressed_proves_legit_wait() -> None:
    slug = trajectory.idle_legitimacy_slug(
        [_owned("t1", "awaiting_qa")],
        {"t1": ("completed", _T0 + timedelta(days=2))},
        _T0,
        _ROT,
        _ROT,
    )
    assert slug == IDLE_WAIT_RESOLVED_AFTER


def test_idle_rotted_task_proves_stranded() -> None:
    slug = trajectory.idle_legitimacy_slug(
        [_owned("t1", "in_progress")],
        {"t1": ("in_progress", _T0 - timedelta(hours=1))},
        _T0,
        _ROT + timedelta(hours=1),
        _ROT,
    )
    assert slug == OWNED_TASK_ROTTED_AFTER


def test_idle_cancelled_after_proves_stranded() -> None:
    slug = trajectory.idle_legitimacy_slug(
        [_owned("t1", "in_progress")],
        {"t1": ("cancelled", _T0 + timedelta(days=3))},
        _T0,
        _ROT,
        _ROT,
    )
    assert slug == OWNED_TASK_ROTTED_AFTER


def test_idle_conflicting_fates_stay_unlabeled() -> None:
    # One owned task delivered, another rotted: the row cannot honestly
    # claim a single option was true.
    slug = trajectory.idle_legitimacy_slug(
        [_owned("t1", "in_progress"), _owned("t2", "in_progress")],
        {
            "t1": _delivered_at(_T0 + timedelta(hours=2)),
            "t2": ("in_progress", _T0 - timedelta(hours=1)),
        },
        _T0,
        _ROT + timedelta(hours=1),
        _ROT,
    )
    assert slug is None


def test_idle_unknown_tasks_stay_unlabeled() -> None:
    slug = trajectory.idle_legitimacy_slug(
        [_owned("t-missing", "in_progress")],
        {},
        _T0,
        _ROT + timedelta(hours=1),
        _ROT,
    )
    assert slug is None


class _FakeRows:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def scalars(self) -> list:
        return self._rows

    def all(self) -> list:
        return self._rows


class _FakeSession:
    """execute() dispatches by statement shape: the caller pre-registers
    the row list and the task-facts list; record_outcome is patched at the
    persist module by the caller."""

    def __init__(self, rows: list, facts: list) -> None:
        self._rows = rows
        self._facts = facts

    async def execute(self, stmt: object) -> object:
        compiled = str(stmt.compile())
        if "decision_log" in compiled:
            return _FakeRows(self._rows)
        return _FakeRows(self._facts)  # tasks select: (id, status, updated_at)

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None


def _row(pilot: str, session_id: str, state: object, created_at: datetime):
    return SimpleNamespace(
        pilot=pilot,
        session_id=session_id,
        state=state,
        created_at=created_at,
        outcome=None,
    )


@pytest.mark.asyncio
async def test_pass_grades_rows_and_stamps_outcomes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    old = datetime.now(UTC) - timedelta(days=30)
    idle_row = _row(
        "idle_legitimacy",
        "idle:be-dev-1",
        {
            "owned_tasks": [
                _owned("11111111-1111-1111-1111-111111111111", "in_progress")
            ]
        },
        old,
    )
    respawn_row = _row(
        "respawn_verdict",
        "respawn:be-dev-1:22222222-2222-2222-2222-222222222222",
        {"task_id": "22222222-2222-2222-2222-222222222222"},
        old,
    )
    t1 = UUID("11111111-1111-1111-1111-111111111111")
    t2 = UUID("22222222-2222-2222-2222-222222222222")
    facts = [
        (t1, "awaiting_qa", old + timedelta(hours=2)),
        (t2, "cancelled", old + timedelta(days=2)),
    ]
    session = _FakeSession([idle_row, respawn_row], facts)
    labeled: list[dict[str, object]] = []

    async def _capture(_session: object, **kwargs: object) -> int:
        labeled.append(dict(kwargs))
        return 1

    monkeypatch.setattr(trajectory.persist, "record_outcome", _capture)
    graded = await trajectory.run_trajectory_pass(session)
    assert graded == 2
    by_pilot = {entry["pilot"]: entry["outcome"] for entry in labeled}
    assert by_pilot["idle_legitimacy"] == OWNED_TASK_SUBMITTED_AFTER
    assert by_pilot["respawn_verdict"] == TASK_CANCELLED_AFTER


@pytest.mark.asyncio
async def test_pass_is_a_noop_when_everything_is_labeled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg.settings, "decisions_enabled", True)
    session = _FakeSession([], [])
    labeled: list[dict[str, object]] = []

    async def _capture(_session: object, **kwargs: object) -> int:
        labeled.append(dict(kwargs))
        return 1

    monkeypatch.setattr(trajectory.persist, "record_outcome", _capture)
    assert await trajectory.run_trajectory_pass(session) == 0
    assert labeled == []


@pytest.mark.asyncio
async def test_engine_loop_disabled_is_a_noop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The labeler rides the master decisions flag: flag off means the
    loop never starts."""
    from roboco.runtime.engines.decisions_labeler import DecisionsLabelerEngine

    monkeypatch.setattr(cfg.settings, "decisions_enabled", False)
    engine = DecisionsLabelerEngine()
    engine._record_loop_heartbeat = lambda *a, **kw: None  # type: ignore[method-assign]
    engine._running = True
    sleep = AsyncMock()
    monkeypatch.setattr("asyncio.sleep", sleep)
    await engine._decisions_labeler_loop()
    sleep.assert_not_awaited()
