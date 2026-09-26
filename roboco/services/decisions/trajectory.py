"""Task-trajectory outcome labeler (spec 12.1 Wave 1).

Grades unlabeled decision_log rows of the trajectory-class pilots against
what the tasks table says happened AFTER the decision:

- idle_legitimacy (choice): the owned tasks' fate proves which option WAS
  true - submitted-after (no new work between idle and submit) proves
  likely-done; rotted/cancelled proves stranded; a resolved wait proves
  legit-wait.
- respawn_verdict (choice): the wedged task's fate after the trip -
  delivered proves spawn, cancelled proves kill-task, stalled past the
  rot horizon proves hold-task-for-human (amended-prompt is never
  auto-derived: the table cannot attribute who amended).
- submit_now_confidence / pm_closure_confidence (score): a needs_revision
  bounce after the decision grades it low (soft gold - signals conflicted,
  they did not predict pole-zero); clean delivery grades the top level.
- plan_quality (score): a bounced plan was inadequate (pole); shipped
  unreplanned is soft between adequate and strong; stalled past the rot
  horizon leans inadequate.
- preflight_diff: graded ONLY on full QA pass (every criterion held,
  hygiene clean). Bounced rows stay unlabeled: per-criterion truth needs
  finding-to-criterion matching (Wave 2).

Design: the fate rules are pure functions (unit-testable without a DB);
this module's DB glue only fetches unlabeled rows, batches task facts,
and stamps labels via ``persist.record_outcome``. Every rule returns at
most ONE slug or None - conflicting fates leave the row unlabeled rather
than guessed. Never raises into the caller.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import select, update

from roboco.config import settings
from roboco.db.tables import DecisionLogTable, TaskReviewFindingTable, TaskTable
from roboco.services.decisions import outcomes, persist
from roboco.services.decisions.outcomes import (
    FLAKE_CONFIRMED_LATER,
    IDLE_WAIT_RESOLVED_AFTER,
    OWNED_TASK_ROTTED_AFTER,
    OWNED_TASK_SUBMITTED_AFTER,
    PLAN_BOUNCED_AFTER,
    PLAN_STALLED_PAST_WINDOW,
    QA_PASSED_AFTER,
    REGRESSION_CONFIRMED_LATER,
    TASK_CANCELLED_AFTER,
    TASK_DELIVERED_AFTER,
    TASK_STALLED_PAST_WINDOW,
)

logger = structlog.get_logger(__name__)

# Task status vocabulary (TaskStatus values) partitioned by what they
# prove about a trajectory.
_DELIVERED = frozenset(
    {
        "awaiting_qa",
        "awaiting_documentation",
        "awaiting_pr_review",
        "awaiting_pm_review",
        "awaiting_ceo_approval",
        "completed",
    }
)
_WAITING = frozenset(
    {
        "awaiting_qa",
        "awaiting_documentation",
        "awaiting_pr_review",
        "awaiting_pm_review",
        "awaiting_ceo_approval",
    }
)
_ACTIVE_UNFINISHED = frozenset(
    {
        "pending",
        "claimed",
        "in_progress",
        "blocked",
        "paused",
        "verifying",
        "needs_revision",
    }
)
_CANCELLED = "cancelled"
_BOUNCED = "needs_revision"

# Per-pass row cap per pilot: the labeler runs every few minutes, so a
# bounded window always drains; the cap only bounds a cold-start backlog.
_ROWS_PER_PILOT = 500

# The single-subject pilots: session_id is "<prefix>:<task_id>" (respawn
# carries an agent segment too, so the task id is the LAST segment).
_SINGLE_SUBJECT_PILOTS = (
    "respawn_verdict",
    "submit_now_confidence",
    "pm_closure_confidence",
    "plan_quality",
    "preflight_diff",
    "second_review_eligibility",
    "assembled_coherence",
)

# Parking label definitions (spec 12.1): a lift inside this window proves
# "a short retry is likely to succeed"; a re-park for the same subject
# inside this window proves the repeated-failure escalation.
_PARK_SHORT_LIFT_MINUTES = 10
_PARK_REPARK_WINDOW_MINUTES = 30

# Triage history window: test-recurrence evidence is scanned over this
# span of triage rows.
_TRIAGE_HISTORY_DAYS = 90


# ---------------------------------------------------------------------------
# Pure fate rules: at most one slug or None. `decision_at` is the row's
# created_at; `tasks_now` maps task id -> (status, updated_at).
# ---------------------------------------------------------------------------


def _moved_after(updated_at: datetime | None, decision_at: datetime) -> bool:
    """True when the task's last change happened AFTER the decision, i.e.
    the trajectory event is evidence about the post-decision world."""
    return updated_at is not None and updated_at > decision_at


def _hit(flag: str | None, *conditions: bool) -> bool:
    """A fate fires only when its slug is configured and every evidence
    condition holds."""
    return flag is not None and all(conditions)


def single_subject_slug(
    facts: tuple[str, datetime | None],
    decision_at: datetime,
    now: datetime,
    rot_after: datetime,
    slugs: _FateSlugs,
) -> str | None:
    """The shared one-task fate rule: at most one fate applies, and a
    fate whose slug the pilot does not grade can never fire."""
    status_now, updated_at = facts
    moved = _moved_after(updated_at, decision_at)
    if _hit(slugs.delivered, status_now in _DELIVERED, moved):
        return slugs.delivered
    if _hit(slugs.cancelled, status_now == _CANCELLED, moved):
        return slugs.cancelled
    if _hit(slugs.bounced, status_now == _BOUNCED, moved):
        return slugs.bounced
    if _hit(
        slugs.stalled,
        status_now in _ACTIVE_UNFINISHED,
        not moved,
        now >= rot_after,
    ):
        return slugs.stalled
    return None


def idle_legitimacy_slug(
    owned: list[dict[str, Any]],
    tasks_now: dict[str, tuple[str, datetime | None]],
    decision_at: datetime,
    now: datetime,
    rot_after: datetime,
) -> str | None:
    """The owned-task fate rule for idle_legitimacy: EXACTLY one fate
    across all owned tasks labels the row; conflicting or absent fates
    leave it unlabeled."""
    candidates: set[str] = set()
    for entry in owned:
        task_id = str(entry.get("task_id") or "")
        facts = tasks_now.get(task_id)
        if not task_id or facts is None:
            continue
        fate = _idle_entry_fate(
            str(entry.get("status") or ""), facts, decision_at, now, rot_after
        )
        if fate:
            candidates.add(fate)
    if len(candidates) == 1:
        return next(iter(candidates))
    return None


def _idle_entry_fate(
    idle_status: str,
    facts: tuple[str, datetime | None],
    decision_at: datetime,
    now: datetime,
    rot_after: datetime,
) -> str | None:
    """One owned task's fate. A wait that ended with work flowing through
    proves legit-wait; unsubmitted work that reached delivery proves
    likely-done; a cancellation or a rotted, untouched task proves
    stranded."""
    status_now, updated_at = facts
    moved = _moved_after(updated_at, decision_at)
    if _hit(OWNED_TASK_ROTTED_AFTER, status_now == _CANCELLED, moved):
        return OWNED_TASK_ROTTED_AFTER
    if _hit(
        IDLE_WAIT_RESOLVED_AFTER,
        status_now in _DELIVERED,
        moved,
        idle_status in _WAITING,
    ):
        return IDLE_WAIT_RESOLVED_AFTER
    if _hit(
        OWNED_TASK_SUBMITTED_AFTER,
        status_now in _DELIVERED,
        moved,
        idle_status in _ACTIVE_UNFINISHED,
    ):
        return OWNED_TASK_SUBMITTED_AFTER
    if _hit(
        OWNED_TASK_ROTTED_AFTER,
        status_now in _ACTIVE_UNFINISHED,
        not moved,
        now >= rot_after,
    ):
        return OWNED_TASK_ROTTED_AFTER
    return None


@dataclass(frozen=True)
class _FateSlugs:
    """The fate slugs a pilot grades. ``None`` = this pilot has no gold
    for that fate, so the fate can never label its rows."""

    delivered: str | None = None
    cancelled: str | None = None
    bounced: str | None = None
    stalled: str | None = None


_FATE_RULES: dict[str, _FateSlugs] = {
    "respawn_verdict": _FateSlugs(
        delivered=TASK_DELIVERED_AFTER,
        cancelled=TASK_CANCELLED_AFTER,
        stalled=TASK_STALLED_PAST_WINDOW,
    ),
    # Score pilots: a bounce is a soft-low gold (signals conflicted, the
    # model did not predict pole-zero), delivery grades the top level.
    "submit_now_confidence": _FateSlugs(
        delivered=QA_PASSED_AFTER, bounced=PLAN_BOUNCED_AFTER
    ),
    "pm_closure_confidence": _FateSlugs(
        delivered=QA_PASSED_AFTER, bounced=PLAN_BOUNCED_AFTER
    ),
    "plan_quality": _FateSlugs(
        delivered=QA_PASSED_AFTER,
        bounced=PLAN_BOUNCED_AFTER,
        stalled=PLAN_STALLED_PAST_WINDOW,
    ),
    # preflight_diff grades pass-only: per-criterion truth on a bounce
    # needs finding-to-criterion matching (Wave 2).
    "preflight_diff": _FateSlugs(delivered=QA_PASSED_AFTER),
    # second_review_eligibility (B9 noul "high-stakes"): a post-gate
    # bounce proves the second review was warranted; clean delivery
    # proves it was not (documented noise: a clean pass could also mean
    # the first review was simply good).
    "second_review_eligibility": _FateSlugs(
        delivered=QA_PASSED_AFTER, bounced=PLAN_BOUNCED_AFTER
    ),
    # assembled_coherence (B43 score): bounced from the gate leans
    # incoherent; through the gate leans coherent.
    "assembled_coherence": _FateSlugs(
        delivered=QA_PASSED_AFTER, bounced=PLAN_BOUNCED_AFTER
    ),
}


def _single_subject_outcome(
    pilot: str,
    facts: tuple[str, datetime | None],
    decision_at: datetime,
    now: datetime,
    rot_after: datetime,
) -> str | None:
    rule = _FATE_RULES.get(pilot)
    if rule is None:
        return None
    return single_subject_slug(facts, decision_at, now, rot_after, rule)


# ---------------------------------------------------------------------------
# DB glue
# ---------------------------------------------------------------------------


def _owned_task_ids(state: Any) -> list[tuple[str, str]]:
    """(task_id, at-decision status) pairs from an idle_legitimacy row's
    state payload; rows without the expected shape contribute nothing."""
    if not isinstance(state, dict):
        return []
    owned = state.get("owned_tasks")
    if not isinstance(owned, list):
        return []
    pairs = []
    for entry in owned:
        if isinstance(entry, dict) and entry.get("task_id"):
            pairs.append((str(entry["task_id"]), str(entry.get("status") or "")))
    return pairs


async def _unlabeled_rows(
    session: Any, pilot: str, older_than: datetime
) -> list[Any]:
    rows = await session.execute(
        select(DecisionLogTable)
        .where(
            DecisionLogTable.pilot == pilot,
            DecisionLogTable.outcome.is_(None),
            DecisionLogTable.question_outcomes.is_(None),
            DecisionLogTable.session_id.is_not(None),
            DecisionLogTable.state.is_not(None),
            DecisionLogTable.created_at.is_not(None),
            DecisionLogTable.created_at < older_than,
        )
        .order_by(DecisionLogTable.created_at.asc())
        .limit(_ROWS_PER_PILOT)
    )
    return list(rows.scalars())


async def _task_facts(
    session: Any, task_ids: list[str]
) -> dict[str, tuple[str, datetime | None]]:
    ids: list[Any] = []
    for raw in task_ids:
        try:
            from uuid import UUID

            ids.append(UUID(raw))
        except ValueError:
            continue
    if not ids:
        return {}
    rows = await session.execute(
        select(TaskTable.id, TaskTable.status, TaskTable.updated_at).where(
            TaskTable.id.in_(ids)
        )
    )
    return {
        str(task_id): (str(status), updated_at)
        for task_id, status, updated_at in rows.all()
    }


async def run_trajectory_pass(session: Any) -> int:
    """One grading pass over every trajectory-class pilot. Returns the
    number of rows labeled; never raises (callers loop this)."""
    now = datetime.now(UTC)
    grade_after = now - timedelta(hours=settings.decisions_trajectory_grade_after_hours)
    rot_after = now - timedelta(hours=settings.decisions_trajectory_rot_hours)
    graded = 0
    graded += await _grade_idle_legitimacy(session, grade_after, rot_after, now)
    graded += await _grade_single_subject(session, grade_after, rot_after, now)
    graded += await _grade_findings_mapping(session, grade_after, rot_after, now)
    graded += await _grade_parking_stale(session, rot_after)
    graded += await _grade_triage_history(session, grade_after)
    return graded


async def _label(
    session: Any, pilot: str, session_id: str, slug: str | None
) -> int:
    if slug is None:
        return 0
    return await persist.record_outcome(
        session, pilot=pilot, session_id=session_id, outcome=slug
    )


async def _grade_idle_legitimacy(
    session: Any, grade_after: datetime, rot_after: datetime, now: datetime
) -> int:
    rows = await _unlabeled_rows(session, "idle_legitimacy", grade_after)
    if not rows:
        return 0
    task_ids: list[str] = []
    for row in rows:
        task_ids.extend(task_id for task_id, _ in _owned_task_ids(row.state))
    facts = await _task_facts(session, task_ids)
    graded = 0
    for row in rows:
        owned = _owned_task_ids(row.state)
        tasks_now = {
            task_id: facts[task_id]
            for task_id, _ in owned
            if task_id in facts
        }
        slug = idle_legitimacy_slug(
            [{"task_id": t, "status": s} for t, s in owned],
            tasks_now,
            row.created_at,
            now,
            rot_after,
        )
        graded += await _label(session, "idle_legitimacy", row.session_id, slug)
    return graded


async def _grade_single_subject(
    session: Any, grade_after: datetime, rot_after: datetime, now: datetime
) -> int:
    graded = 0
    for pilot in _SINGLE_SUBJECT_PILOTS:
        rows = await _unlabeled_rows(session, pilot, grade_after)
        if not rows:
            continue
        task_ids = [
            str(row.session_id).rsplit(":", 1)[-1] for row in rows
        ]
        facts = await _task_facts(session, task_ids)
        for row in rows:
            task_id = str(row.session_id).rsplit(":", 1)[-1]
            slug = _single_subject_outcome(
                pilot, facts.get(task_id, ("", None)), row.created_at, now, rot_after
            )
            graded += await _label(session, pilot, row.session_id, slug)
    return graded


# ---------------------------------------------------------------------------
# Wave 2 graders: findings ledger, parking stale sweep, triage history.
# ---------------------------------------------------------------------------


def _finding_fate(
    status_now: str,
    updated_at: datetime | None,
    decision_at: datetime,
    now: datetime,
    rot_after: datetime,
) -> str | None:
    """One finding's fate from its ledger state: verified (or addressed
    after the decision) proves the diff addressed it; still open past the
    rot horizon proves it re-raised; waived is recorded but unusable;
    anything in between stays unlabeled."""
    if status_now == "waived":
        return "waived"
    if status_now == "verified":
        return "resolved"
    if (
        status_now == "addressed"
        and _moved_after(updated_at, decision_at)
    ):
        return "resolved"
    if (
        status_now == "open"
        and not _moved_after(updated_at, decision_at)
        and now >= rot_after
    ):
        return "re_raised"
    return None


def _findings_fates(
    state: Any, facts: dict[str, tuple[str, datetime | None]],
    decision_at: datetime, now: datetime, rot_after: datetime,
) -> dict[str, str]:
    """{question_key: fate} for one findings_mapping row's state."""
    if not isinstance(state, dict) or not isinstance(state.get("findings"), list):
        return {}
    fates: dict[str, str] = {}
    for entry in state["findings"]:
        if not isinstance(entry, dict):
            continue
        finding_id = str(entry.get("id") or "")
        idx = entry.get("idx")
        if not finding_id or idx is None or finding_id not in facts:
            continue
        fate = _finding_fate(*facts[finding_id], decision_at, now, rot_after)
        if fate:
            fates[f"finding_{idx}"] = fate
    return fates


def _collect_finding_ids(rows: list[Any]) -> list[Any]:
    """UUID-parse every finding id referenced by the rows' states."""
    ids: list[Any] = []
    for row in rows:
        for entry in (row.state or {}).get("findings") or []:
            if isinstance(entry, dict) and entry.get("id"):
                with contextlib.suppress(ValueError):
                    ids.append(UUID(str(entry["id"])))
    return ids


async def _grade_findings_mapping(
    session: Any, grade_after: datetime, rot_after: datetime, now: datetime
) -> int:
    rows = await _unlabeled_rows(session, "findings_mapping", grade_after)
    if not rows:
        return 0
    finding_ids = _collect_finding_ids(rows)
    facts: dict[str, tuple[str, datetime | None]] = {}
    if finding_ids:
        result = await session.execute(
            select(
                TaskReviewFindingTable.id,
                TaskReviewFindingTable.status,
                TaskReviewFindingTable.updated_at,
            ).where(TaskReviewFindingTable.id.in_(finding_ids))
        )
        facts = {
            str(finding_id): (str(status), updated_at)
            for finding_id, status, updated_at in result.all()
        }
    graded = 0
    for row in rows:
        fates = _findings_fates(row.state, facts, row.created_at, now, rot_after)
        if not fates:
            continue
        graded += await persist.record_question_outcomes(
            session,
            pilot="findings_mapping",
            session_id=str(row.session_id),
            fates=fates,
        )
    return graded


async def _grade_parking_stale(session: Any, rot_after: datetime) -> int:
    """Retire parking rows past the rot horizon with no lift evidence:
    ambiguous (the probe path may simply never have stamped), so they get
    the unusable slug and stop being rescanned."""
    rows = await _unlabeled_rows(session, "parking", rot_after)
    graded = 0
    for row in rows:
        graded += await persist.record_outcome(
            session,
            pilot="parking",
            session_id=str(row.session_id),
            outcome=outcomes.STALE_UNRESOLVED,
        )
    return graded


def _norm_test(name: str) -> str:
    return " ".join(str(name or "").lower().split())


def _files_overlap(a: list[str], b: list[str]) -> bool:
    """Loose POSIX-style overlap between two changed-file lists (same
    matching family as the findings mapper's file overlap)."""
    norm_a = {f.replace("\\", "/").strip("/").lower() for f in a if f}
    norm_b = {f.replace("\\", "/").strip("/").lower() for f in b if f}
    for path_a in norm_a:
        for path_b in norm_b:
            if path_a == path_b or path_a.endswith(path_b) or path_b.endswith(path_a):
                return True
    return False


def _later_independent_failure(
    same_test: list[dict[str, Any]],
    own_files: list[str],
    own_task_id: str,
    decision_at: datetime,
) -> bool:
    """True when the test failed again after the decision on a task whose
    diff shares no files with this one: an independent failure."""
    return any(
        h["task_id"] != own_task_id
        and h["created_at"] > decision_at
        and not _files_overlap(h["changed_files"], own_files)
        for h in same_test
    )


def _any_later_failure(
    same_test: list[dict[str, Any]],
    own_task_id: str,
    decision_at: datetime,
) -> bool:
    return any(
        h["task_id"] != own_task_id and h["created_at"] > decision_at
        for h in same_test
    )


def triage_slug(
    subject: tuple[str, list[str], str],
    history: list[dict[str, Any]],
    own_task_facts: tuple[str, datetime | None] | None,
    decision_at: datetime,
) -> str | None:
    """The triage cause rule from the test's later history (pure).

    - The same test failing later on tasks whose diffs share no files
      with this one proves the failure was independent: flaky.
    - No later failure of that test anywhere AND this task delivered:
      the failure died with this diff: my_regression.
    - Anything else stays unlabeled (environment is never auto-derived).
    """
    test_name, own_files, own_task_id = subject
    test = _norm_test(test_name)
    same_test = [h for h in history if _norm_test(h["test_name"]) == test]
    if _later_independent_failure(same_test, own_files, own_task_id, decision_at):
        return FLAKE_CONFIRMED_LATER
    if own_task_facts is not None:
        status_now, updated_at = own_task_facts
        if (
            status_now in _DELIVERED
            and _moved_after(updated_at, decision_at)
            and not _any_later_failure(same_test, own_task_id, decision_at)
        ):
            return REGRESSION_CONFIRMED_LATER
    return None


def _triage_history_entry(row: Any) -> dict[str, Any]:
    state = row.state if isinstance(row.state, dict) else {}
    return {
        "task_id": str(row.session_id or "").rsplit(":", 1)[-1],
        "test_name": str(state.get("test_name") or ""),
        "changed_files": [
            str(f) for f in state.get("changed_files_in_diff") or []
        ],
        "created_at": row.created_at,
    }


async def _grade_triage_history(session: Any, grade_after: datetime) -> int:
    now = datetime.now(UTC)
    history_since = now - timedelta(days=_TRIAGE_HISTORY_DAYS)
    rows = await _unlabeled_rows(session, "triage_failure", grade_after)
    if not rows:
        return 0
    result = await session.execute(
        select(DecisionLogTable)
        .where(
            DecisionLogTable.pilot == "triage_failure",
            DecisionLogTable.created_at >= history_since,
        )
        .order_by(DecisionLogTable.created_at.asc())
        .limit(2000)
    )
    history = [
        _triage_history_entry(row) for row in result.scalars()
    ]
    task_ids = [
        str(row.session_id).rsplit(":", 1)[-1]
        for row in rows
    ]
    facts = await _task_facts(session, task_ids)
    graded = 0
    for row in rows:
        task_id = str(row.session_id).rsplit(":", 1)[-1]
        state = row.state if isinstance(row.state, dict) else {}
        subject = (
            str(state.get("test_name") or ""),
            [str(f) for f in state.get("changed_files_in_diff") or []],
            task_id,
        )
        slug = triage_slug(
            subject, history, facts.get(task_id), row.created_at
        )
        graded += await _label(session, "triage_failure", row.session_id, slug)
    return graded


# ---------------------------------------------------------------------------
# In-process event stamps: called at the moment evidence is created, not
# swept. Own short-lived session, best-effort, like the parking path.
# ---------------------------------------------------------------------------


async def stamp_parking_lift(provider: str, kind: str, agent_id: str) -> None:
    """Grade this subject's unlabeled parking rows when the provider
    probe lifts: a lift inside the short-retry window proves retry_soon,
    a later lift proves park_standard. Best-effort, own session."""
    from roboco.db.base import get_session_factory

    try:
        session_factory = get_session_factory()
        async with session_factory() as db:
            now = datetime.now(UTC)
            rows = await _unlabeled_rows(db, "parking", now)
            session_id = f"parking:{provider}:{kind}:{agent_id}"
            graded = 0
            for row in rows:
                if row.session_id != session_id:
                    continue
                age = now - row.created_at if row.created_at else timedelta()
                slug = (
                    outcomes.LIMIT_LIFTED_QUICKLY
                    if age <= timedelta(minutes=_PARK_SHORT_LIFT_MINUTES)
                    else outcomes.LIMIT_LIFTED_AFTER_COOLDOWN
                )
                graded += await persist.record_outcome(
                    db, pilot="parking", session_id=session_id, outcome=slug
                )
            await db.commit()
        if graded:
            logger.info(
                "parking rows graded by provider lift",
                provider=provider,
                rows=graded,
            )
    except Exception as exc:
        logger.debug("parking lift stamp skipped", error=str(exc))


async def stamp_parking_re_limit(provider: str, kind: str, agent_id: str) -> None:
    """Grade this subject's unlabeled parking rows when a resume bails on
    a re-limit: repeated failure, the escalate option was true."""
    from roboco.db.base import get_session_factory

    try:
        session_factory = get_session_factory()
        async with session_factory() as db:
            await persist.record_outcome(
                db,
                pilot="parking",
                session_id=f"parking:{provider}:{kind}:{agent_id}",
                outcome=outcomes.RE_LIMITED_ON_RESUME,
            )
            await db.commit()
    except Exception as exc:
        logger.debug("parking re-limit stamp skipped", error=str(exc))


async def stamp_parking_repark(session: Any, session_id: str) -> int:
    """Called from the parking decision path: a fresh park for a subject
    that parked again within the repark window is exactly the
    repeated-failure evidence the escalate option describes. Grades only
    the rows inside that window (the just-written decision row is
    excluded by the freshness floor); never raises."""
    now = datetime.now(UTC)
    window_open = now - timedelta(minutes=_PARK_REPARK_WINDOW_MINUTES)
    fresh_floor = now - timedelta(seconds=5)
    try:
        async with session.begin_nested():
            result = await session.execute(
                update(DecisionLogTable)
                .where(
                    DecisionLogTable.pilot == "parking",
                    DecisionLogTable.session_id == session_id[:280],
                    DecisionLogTable.outcome.is_(None),
                    DecisionLogTable.created_at >= window_open,
                    DecisionLogTable.created_at <= fresh_floor,
                )
                .values(
                    outcome="re_limited_on_resume",
                    outcome_at=now,
                )
            )
        return int(result.rowcount or 0)
    except Exception as exc:
        logger.debug(
            "parking repark stamp skipped",
            session_id=session_id,
            error=str(exc),
        )
        return 0
