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


_GRADED_PILOTS = {
    "idle_legitimacy",
    "respawn_verdict",
    "submit_now_confidence",
    "pm_closure_confidence",
    "plan_quality",
    "preflight_diff",
    "second_review_eligibility",
    "assembled_coherence",
    "findings_mapping",
    "parking",
    "triage_failure",
}


class _FakeSession:
    """execute() dispatches by statement shape AND pilot bound param, so
    each pilot's grader sees only its own rows; the tasks select returns
    the registered task facts. record_outcome is patched at the persist
    module by the caller."""

    def __init__(self, rows: list, facts: list) -> None:
        self._rows = rows
        self._facts = facts

    def _result_for(self, stmt: object) -> _FakeRows:
        compiled = str(stmt.compile())
        if compiled.lstrip().upper().startswith("UPDATE"):
            return _FakeRows([])
        if "decision_log" in compiled:
            pilot = next(
                (
                    v
                    for v in stmt.compile().params.values()
                    if isinstance(v, str) and v in _GRADED_PILOTS
                ),
                None,
            )
            matching = (
                [r for r in self._rows if r.pilot == pilot]
                if pilot
                else self._rows
            )
            return _FakeRows(matching)
        return _FakeRows(self._facts)

    async def execute(self, stmt: object) -> _FakeRows:
        return self._result_for(stmt)

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


# ---------------------------------------------------------------------------
# Wave 2: findings fates, triage history, parking stamps
# ---------------------------------------------------------------------------


def test_finding_fate_resolved_by_verification() -> None:
    assert (
        trajectory._finding_fate("verified", None, _T0, _ROT, _ROT)
        == "resolved"
    )


def test_finding_fate_addressed_after_decision() -> None:
    updated = _T0 + timedelta(days=1)
    assert (
        trajectory._finding_fate("addressed", updated, _T0, _ROT, _ROT)
        == "resolved"
    )


def test_finding_fate_still_open_past_rot_is_re_raised() -> None:
    assert (
        trajectory._finding_fate("open", None, _T0, _ROT + timedelta(days=1), _ROT)
        == "re_raised"
    )


def test_finding_fate_waived_is_recorded_but_unusable() -> None:
    assert trajectory._finding_fate("waived", None, _T0, _ROT, _ROT) == "waived"
    from roboco.services.decisions.outcomes import question_fate_gold

    assert question_fate_gold("findings_mapping", "waived") is None
    assert question_fate_gold("findings_mapping", "resolved") == {
        "true": 1.0,
        "false": 0.0,
    }


def test_findings_fates_skip_unknown_findings() -> None:
    state = {"findings": [{"idx": 0, "id": "f0"}, {"idx": 1, "id": "f1"}]}
    fates = trajectory._findings_fates(
        state,
        {"f0": ("verified", None)},
        _T0,
        _ROT,
        _ROT,
    )
    assert fates == {"finding_0": "resolved"}


def test_triage_flake_confirmed_by_independent_later_failure() -> None:
    history = [
        {
            "task_id": "other-task",
            "test_name": "test_flaky_login",
            "changed_files": ["panel/other.ts"],
            "created_at": _T0 + timedelta(days=1),
        }
    ]
    slug = trajectory.triage_slug(
        ("test_flaky_login", ["backend/auth.py"], "this-task"),
        history,
        ("awaiting_qa", _T0 + timedelta(days=2)),
        _T0,
    )
    assert slug == "flake_confirmed_by_later_failures"


def test_triage_regression_confirmed_when_no_recurrence() -> None:
    slug = trajectory.triage_slug(
        ("test_logout_crash", ["backend/auth.py"], "this-task"),
        [],
        ("completed", _T0 + timedelta(days=3)),
        _T0,
    )
    assert slug == "regression_confirmed_no_recurrence"


def test_triage_overlapping_later_failure_is_not_flake_evidence() -> None:
    history = [
        {
            "task_id": "other-task",
            "test_name": "test_logout_crash",
            "changed_files": ["backend/auth.py"],
            "created_at": _T0 + timedelta(days=1),
        }
    ]
    slug = trajectory.triage_slug(
        ("test_logout_crash", ["backend/auth.py"], "this-task"),
        history,
        None,
        _T0,
    )
    assert slug is None


@pytest.mark.asyncio
async def test_parking_stale_rows_get_retired_not_escalated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = datetime.now(UTC) - timedelta(days=30)
    row = _row("parking", "parking:anthropic:rate-limit:be-dev-1", {}, old)
    session = _FakeSession([row], [])
    labeled: list[dict[str, object]] = []

    async def _capture(_session: object, **kwargs: object) -> int:
        labeled.append(dict(kwargs))
        return 1

    monkeypatch.setattr(trajectory.persist, "record_outcome", _capture)
    graded = await trajectory._grade_parking_stale(
        session, datetime.now(UTC) - timedelta(hours=1)
    )
    assert graded == 1
    assert labeled[0]["outcome"] == "stale_unresolved"


class _FakeNested:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> None:
        return None


class _LiftSession(_FakeSession):
    """Adds the savepoint + UPDATE surface record_outcome uses."""

    def __init__(self, rows: list) -> None:
        super().__init__(rows, [])

    async def __aenter__(self) -> "_LiftSession":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def commit(self) -> None:
        return None

    def begin_nested(self) -> _FakeNested:
        return _FakeNested()

    async def execute(self, stmt: object) -> object:
        compiled = str(stmt.compile())
        if compiled.lstrip().upper().startswith("UPDATE"):
            return SimpleNamespace(rowcount=1)
        return await super().execute(stmt)


@pytest.mark.asyncio
async def test_parking_lift_stamp_splits_on_latency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lift inside the short-retry window grades retry_soon; a later
    lift grades park_standard."""
    from roboco.services.decisions import outcomes

    now = datetime.now(UTC)
    quick = _row(
        "parking",
        "parking:anthropic:rate-limit:be-dev-1",
        {},
        now - timedelta(minutes=3),
    )
    slow = _row(
        "parking",
        "parking:anthropic:rate-limit:fe-dev-2",
        {},
        now - timedelta(hours=2),
    )
    stamped: list[str] = []

    async def _capture(_session: object, **kwargs: object) -> int:
        stamped.append(str(kwargs.get("outcome")))
        return 1

    monkeypatch.setattr(trajectory.persist, "record_outcome", _capture)
    import roboco.db.base as db_base

    monkeypatch.setattr(
        db_base,
        "get_session_factory",
        lambda: lambda: _LiftSession([quick, slow]),
    )
    await trajectory.stamp_parking_lift("anthropic", "rate-limit", "be-dev-1")
    await trajectory.stamp_parking_lift("anthropic", "rate-limit", "fe-dev-2")
    assert stamped == [
        outcomes.LIMIT_LIFTED_QUICKLY,
        outcomes.LIMIT_LIFTED_AFTER_COOLDOWN,
    ]
