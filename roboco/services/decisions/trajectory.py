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
    graded += await _grade_size(session, grade_after)
    graded += await _grade_heal_severity(session, grade_after)
    graded += await _grade_ci_history(session, grade_after, rot_after)
    graded += await _grade_stranded(session, grade_after, rot_after, now)
    graded += await _grade_collision_edge(session, grade_after)
    graded += await _grade_memory_links(session, grade_after, rot_after, now)
    # History retention (best-effort): CI readings and retrieval events
    # past their windows stop being useful and stop the tables growing.
    try:
        from roboco.services import ci_history

        await ci_history.prune(session)
    except Exception as exc:
        logger.debug("ci history prune failed", error=str(exc))
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


def _finding_refs(state: Any) -> list[tuple[int, str]]:
    """(idx, finding id) pairs a findings row asked about."""
    if not isinstance(state, dict) or not isinstance(state.get("findings"), list):
        return []
    refs: list[tuple[int, str]] = []
    for entry in state["findings"]:
        if isinstance(entry, dict) and entry.get("id") and entry.get("idx") is not None:
            refs.append((int(entry["idx"]), str(entry["id"])))
    return refs


def _findings_fates(
    state: Any,
    facts: dict[str, tuple[str, datetime | None]],
    decision_at: datetime,
    now: datetime,
    rot_after: datetime,
) -> dict[str, str]:
    """{question_key: fate} for one findings_mapping row's state."""
    fates: dict[str, str] = {}
    for idx, finding_id in _finding_refs(state):
        if finding_id not in facts:
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


def _rel_path(path: str) -> str:
    return str(path).replace("\\", "/").strip("/").lower()


def _files_overlap(a: list[str], b: list[str]) -> bool:
    """Loose POSIX-style overlap between two changed-file lists (same
    matching family as the findings mapper's file overlap)."""
    norm_a = {_rel_path(f) for f in a}
    norm_b = {_rel_path(f) for f in b}
    return any(
        x == y or x.endswith(y) or y.endswith(x) for x in norm_a for y in norm_b
    )


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


# ---------------------------------------------------------------------------
# Wave 3 graders: realized-size golds, CI run-history joins, and the
# secretary confirmation stamp.
# ---------------------------------------------------------------------------


_SIZE_HEAVY_COMMITS = 8
_SIZE_HEAVY_DURATION = timedelta(hours=96)
_SIZE_LIGHT_COMMITS = 2
_SIZE_LIGHT_DURATION = timedelta(hours=24)


def size_slug(
    commits: int, duration: timedelta | None, *, delivered: bool
) -> str | None:
    """The realized-size fate of a delivered task (pure). Pole thresholds:
    heavy means >= 8 commits or >= 4 days of work; light means <= 2
    commits inside a day. Everything else was standard."""
    if not delivered:
        return None
    if commits >= _SIZE_HEAVY_COMMITS or (
        duration is not None and duration >= _SIZE_HEAVY_DURATION
    ):
        return outcomes.REALIZED_HEAVY
    if commits <= _SIZE_LIGHT_COMMITS and (
        duration is None or duration <= _SIZE_LIGHT_DURATION
    ):
        return outcomes.REALIZED_LIGHT
    return outcomes.REALIZED_STANDARD


async def _size_facts(
    session: Any, task_ids: list[str]
) -> dict[str, tuple[str, datetime | None, datetime | None, int]]:
    """(status, updated_at, created_at, commit_count) per task id."""
    ids: list[Any] = []
    for raw in task_ids:
        with contextlib.suppress(ValueError):
            ids.append(UUID(raw))
    if not ids:
        return {}
    rows = await session.execute(
        select(
            TaskTable.id,
            TaskTable.status,
            TaskTable.updated_at,
            TaskTable.created_at,
            TaskTable.commits,
        ).where(TaskTable.id.in_(ids))
    )
    return {
        str(task_id): (str(status), updated_at, created_at, len(commits or []))
        for task_id, status, updated_at, created_at, commits in rows.all()
    }


async def _grade_size(session: Any, grade_after: datetime) -> int:
    """complexity + heal_severity: the delivered work's realized size
    grades the size/severity prediction (documented softness: commit
    count and elapsed time approximate weight)."""
    graded = 0
    rows = await _unlabeled_rows(session, "complexity", grade_after)
    facts = await _size_facts(
        session, [str(r.session_id).rsplit(":", 1)[-1] for r in rows]
    )
    for row in rows:
        task_id = str(row.session_id).rsplit(":", 1)[-1]
        slug = _size_fate(facts.get(task_id), row.created_at)
        graded += await _label(session, "complexity", row.session_id, slug)
    return graded


async def _grade_heal_severity(
    session: Any, grade_after: datetime
) -> int:
    rows = await _unlabeled_rows(session, "heal_severity", grade_after)
    fix_tasks = await _heal_fix_tasks_by_fingerprint(session)
    graded = 0
    for row in rows:
        fingerprint = str(row.session_id).rsplit(":", 1)[-1]
        slug = _size_fate(fix_tasks.get(fingerprint), row.created_at)
        graded += await _label(session, "heal_severity", row.session_id, slug)
    return graded


def _size_fate(
    entry: tuple[str, datetime | None, datetime | None, int] | None,
    decision_at: datetime,
) -> str | None:
    if entry is None:
        return None
    status_now, updated_at, created_at, commits = entry
    duration = updated_at - created_at if updated_at and created_at else None
    return size_slug(
        commits,
        duration,
        delivered=status_now in _DELIVERED
        and _moved_after(updated_at, decision_at),
    )


async def _heal_fix_tasks_by_fingerprint(
    session: Any,
) -> dict[str, tuple[str, datetime | None, datetime | None, int]]:
    """Map each self-heal fix task's fingerprint to its size facts. The
    bounded scan is fine at fleet scale: self-heal opens few tasks, each
    deduped per fingerprint."""
    from roboco.services.task import (
        SELF_HEAL_SOURCE,
        extract_self_heal_fingerprint,
    )

    result = await session.execute(
        select(TaskTable)
        .where(TaskTable.source == SELF_HEAL_SOURCE)
        .order_by(TaskTable.created_at.desc())
        .limit(300)
    )
    mapping: dict[str, tuple[str, datetime | None, datetime | None, int]] = {}
    for task in result.scalars():
        fingerprint = extract_self_heal_fingerprint(task)
        if not fingerprint:
            continue
        mapping[fingerprint] = (
            str(task.status),
            task.updated_at,
            task.created_at,
            len(task.commits or []),
        )
    return mapping


_HARD_RED_RUNS = 2


def _ci_watch_urgency_slug(runs: list[Any]) -> str | None:
    """Green-first proves flake; repeated reds prove a hard regression;
    anything else waits for more history."""
    if not runs:
        return None
    if not runs[0].is_breach:
        return outcomes.CI_FLAKED
    if sum(1 for r in runs if r.is_breach) >= _HARD_RED_RUNS:
        return outcomes.CI_HARD_RED
    return None


def _dep_update_risk_slug(runs: list[Any]) -> str | None:
    """Any post-bump failure proves high risk; a clean run sequence
    proves low; no runs prove nothing."""
    if not runs:
        return None
    if any(r.is_breach for r in runs):
        return outcomes.UPDATE_CAUSED_FAILURES
    return outcomes.UPDATE_RAN_CLEAN


async def _grade_ci_history(
    session: Any, grade_after: datetime, rot_after: datetime
) -> int:
    """ci_watch_route urgency + dep_update_risk from persisted run
    sequences (the reason ci_runs exists)."""
    from roboco.services import ci_history

    graded = 0
    rows = await _unlabeled_rows(session, "ci_watch_route", grade_after)
    for row in rows:
        project = str(row.session_id).rsplit(":", 1)[-1]
        runs = await ci_history.runs_for(session, project, row.created_at, rot_after)
        slug = _ci_watch_urgency_slug(runs)
        graded += await _label(session, "ci_watch_route", row.session_id, slug)
    rows = await _unlabeled_rows(session, "dep_update_risk", grade_after)
    for row in rows:
        project = str(row.session_id).rsplit(":", 1)[-1]
        runs = await ci_history.runs_for(session, project, row.created_at, rot_after)
        slug = _dep_update_risk_slug(runs)
        graded += await _label(session, "dep_update_risk", row.session_id, slug)
    return graded


async def stamp_secretary_confirmation(
    session: Any, payload: dict[str, Any], kind: str
) -> int:
    """The CEO ran a pilot-filled directive unchanged: the picked kind was
    right (spec 12.1). Rejections are ambiguous and stay unlabeled."""
    from roboco.services.decisions.pilots import state_key
    from roboco.services.decisions.pilots_content import _STATE_TEXT_CAP

    utterance = " ".join(
        str(v).strip() for v in (payload or {}).values() if isinstance(v, str)
    ).strip()
    if not utterance:
        return 0
    key = state_key({"utterance": utterance[:_STATE_TEXT_CAP]})
    session_id = f"secretary:kind:{key}"
    return await persist.record_question_outcomes(
        session,
        pilot="secretary_nl",
        session_id=session_id,
        fates={"gate": f"confirmed:{kind}"},
    )


# ---------------------------------------------------------------------------
# Wave 4 graders: stranded batches, collision overlap, release decisions.
# ---------------------------------------------------------------------------


def _stranded_titles(rows: list[Any]) -> list[str]:
    """Every blocked-task title referenced by the rows' states."""
    titles: list[str] = []
    for row in rows:
        state = row.state if isinstance(row.state, dict) else {}
        titles.extend(str(t) for t in state.get("blocked_task_titles") or [])
    return titles


def _stranded_fate(
    titles: list[str],
    facts: dict[str, tuple[str, datetime | None]],
    decision_at: datetime,
    now: datetime,
    rot_after: datetime,
) -> str | None:
    """The blocked batch's fate: every matched task cleared = the wait
    was right; every matched task still stuck past the rot horizon (or
    cancelled by a human) = escalation was warranted; mixed or absent =
    unlabeled."""
    candidates = {
        fate
        for fate in (
            _stranded_title_fate(facts.get(title), decision_at, now, rot_after)
            for title in titles
        )
        if fate
    }
    if len(candidates) == 1:
        return next(iter(candidates))
    return None


def _stranded_title_fate(
    entry: tuple[str, datetime | None] | None,
    decision_at: datetime,
    now: datetime,
    rot_after: datetime,
) -> str | None:
    """One blocked task's fate: cleared after the decision proves the
    wait was right; human-cancelled or stuck past the rot horizon proves
    escalation was warranted; anything else proves nothing."""
    if entry is None:
        return None
    status_now, updated_at = entry
    moved = _moved_after(updated_at, decision_at)
    if status_now in _DELIVERED and moved:
        return "batch_recovered_after"
    if status_now == "cancelled" and moved:
        # A human cancelled it: the strand needed exactly the authority
        # the escalate option describes.
        return "still_stranded_past_window"
    if status_now in _ACTIVE_UNFINISHED and not moved and now >= rot_after:
        return "still_stranded_past_window"
    return None


async def _grade_stranded(
    session: Any, grade_after: datetime, rot_after: datetime, now: datetime
) -> int:
    rows = await _unlabeled_rows(session, "stranded_response", grade_after)
    if not rows:
        return 0
    titles = _stranded_titles(rows)
    if not titles:
        return 0
    result = await session.execute(
        select(TaskTable.title, TaskTable.status, TaskTable.updated_at).where(
            TaskTable.title.in_(titles)
        )
    )
    facts = {
        str(title): (str(status), updated_at)
        for title, status, updated_at in result.all()
    }
    graded = 0
    for row in rows:
        slug = _stranded_fate(
            _stranded_titles([row]),
            facts,
            row.created_at,
            now,
            rot_after,
        )
        graded += await _label(
            session, "stranded_response", str(row.session_id), slug
        )
    return graded


def _pair_fates(
    pairs: list[Any],
    facts: dict[str, tuple[str, datetime | None, set[str]]],
    decision_at: datetime,
) -> dict[str, str]:
    """Per-pair fates from the delivered diffs' actual file overlap: both
    tasks delivered and their commit files intersect proves the edge; both
    delivered with no intersection disproves it; anything else waits."""
    fates: dict[str, str] = {}
    for idx, pair in enumerate(pairs):
        if not isinstance(pair, dict):
            continue
        fate = _pair_fate(pair, facts, decision_at)
        if fate:
            fates[f"pair_{idx}"] = fate
    return fates


def _delivered_since(
    fact: tuple[str, datetime | None, set[str]] | None,
    decision_at: datetime,
) -> bool:
    """True when a fact's task is delivered and moved after the mark."""
    return fact is not None and fact[0] in _DELIVERED and _moved_after(
        fact[1], decision_at
    )


def _pair_fate(
    pair: dict[str, Any],
    facts: dict[str, tuple[str, datetime | None, set[str]]],
    decision_at: datetime,
) -> str | None:
    """One pair's fate: both sides delivered and their commit files
    intersect proves the edge; both delivered with no intersection
    disproves it; anything else waits for more evidence."""
    left = pair.get("left") or {}
    right = pair.get("right") or {}
    left_fact = facts.get(str(left.get("id") or ""))
    right_fact = facts.get(str(right.get("id") or ""))
    left_ok = _delivered_since(left_fact, decision_at)
    right_ok = _delivered_since(right_fact, decision_at)
    if not (left_ok and right_ok):
        return None
    return (
        "overlap_confirmed" if left_fact[2] & right_fact[2] else "no_overlap"
    )


def _pair_sides(state: Any) -> list[str]:
    """Every task id referenced by one row's collision pairs."""
    if not isinstance(state, dict) or not isinstance(state.get("pairs"), list):
        return []
    ids: list[str] = []
    for pair in state["pairs"]:
        if not isinstance(pair, dict):
            continue
        for side in ("left", "right"):
            value = (pair.get(side) or {}).get("id")
            if value:
                ids.append(str(value))
    return ids


def _pair_task_ids(rows: list[Any]) -> list[str]:
    """Every task id referenced by the rows' collision pairs."""
    task_ids: list[str] = []
    for row in rows:
        task_ids.extend(_pair_sides(row.state))
    return task_ids


async def _grade_collision_edge(session: Any, grade_after: datetime) -> int:
    rows = await _unlabeled_rows(session, "collision_edge", grade_after)
    if not rows:
        return 0
    task_ids = _pair_task_ids(rows)
    facts = await _delivered_files(session, task_ids)
    graded = 0
    for row in rows:
        state = row.state if isinstance(row.state, dict) else {}
        fates = _pair_fates(state.get("pairs") or [], facts, row.created_at)
        if not fates:
            continue
        graded += await persist.record_question_outcomes(
            session,
            pilot="collision_edge",
            session_id=str(row.session_id),
            fates=fates,
        )
    return graded


async def _delivered_files(
    session: Any, task_ids: list[str]
) -> dict[str, tuple[str, datetime | None, set[str]]]:
    """(status, updated_at, changed-file set) per task id, from the
    tasks' recorded commit files."""
    ids: list[Any] = []
    for raw in task_ids:
        with contextlib.suppress(ValueError):
            ids.append(UUID(raw))
    if not ids:
        return {}
    rows = await session.execute(
        select(
            TaskTable.id, TaskTable.status, TaskTable.updated_at, TaskTable.commits
        ).where(TaskTable.id.in_(ids))
    )
    out: dict[str, tuple[str, datetime | None, set[str]]] = {}
    for task_id, status, updated_at, commits in rows.all():
        files: set[str] = set()
        for commit in commits or []:
            commit_files = commit.get("files") if isinstance(commit, dict) else None
            if isinstance(commit_files, list):
                files.update(str(f) for f in commit_files)
        out[str(task_id)] = (str(status), updated_at, files)
    return out


async def stamp_release_decision(
    session: Any,
    *,
    decision: str,
    change_summaries: list[str],
    bump_kind: str,
    gap_count: int,
) -> int:
    """Grade the release_worthy and release_readiness rows of one
    proposal when the CEO decides it: approve proves the urgency call
    and confirms the flagged risks were acceptable; reject-with-changes
    proves the opposite. Recomputes the pilots' state keys EXACTLY as
    the pilots built them (same caps, same derived fields) so the labels
    land on the decision rows. Best-effort; unlabeled rows stay
    candidates."""
    from roboco.services.decisions.pilots import state_key

    if not settings.decisions_enabled:
        return 0
    summaries = [s[:200] for s in list(change_summaries)[:30]]
    worthy_state = {
        "bump_kind": bump_kind,
        "commit_count": len(summaries),
        "commit_floor": settings.release_min_commits,
        "change_summary": summaries,
    }
    readiness_state = {
        "bump_kind": bump_kind,
        "open_gap_count": gap_count,
        "change_summary": summaries,
    }
    worthy_key = f"release:worthy:{state_key(worthy_state)}"
    readiness_key = f"readiness:risk:{state_key(readiness_state)}"
    graded = 0
    if decision == "approve":
        graded += await persist.record_outcome(
            session,
            pilot="release_worthy",
            session_id=worthy_key,
            outcome=outcomes.WORTHY_CONFIRMED,
        )
        graded += await persist.record_outcome(
            session,
            pilot="release_readiness",
            session_id=readiness_key,
            outcome=outcomes.RISK_CONFIRMED_LOW,
        )
    elif decision == "reject":
        graded += await persist.record_outcome(
            session,
            pilot="release_worthy",
            session_id=worthy_key,
            outcome=outcomes.NOT_WORTHY_YET,
        )
        graded += await persist.record_outcome(
            session,
            pilot="release_readiness",
            session_id=readiness_key,
            outcome=outcomes.RISK_CONFIRMED_HIGH,
        )
    return graded


# ---------------------------------------------------------------------------
# Wave 5: memory-family links and retrieval grading.
# ---------------------------------------------------------------------------


def learning_source_uri(content: str) -> str:
    """The source URI a learning is indexed under: deterministic from the
    content per the optimal learnings plugin's documented scheme
    (``roboco://learnings/lrn-{md5(content[:100])[:12]}``), so the link
    writer and the retrieval log agree without sharing state."""
    import hashlib

    digest = hashlib.md5(content[:100].encode("utf-8")).hexdigest()[:12]
    return f"roboco://learnings/lrn-{digest}"


async def link_memory_source(pilot: str, session_id: str, source: str) -> None:
    """Record that one decision produced one knowledge item. Own
    short-lived session, best-effort: the producer's path must never wait
    on or fail for the corpus."""
    from roboco.db.base import get_session_factory
    from roboco.db.tables import MemoryLinkTable

    if not session_id or not source:
        return
    try:
        session_factory = get_session_factory()
        async with session_factory() as db:
            db.add(
                MemoryLinkTable(
                    pilot=pilot[:60],
                    session_id=session_id[:280],
                    source=source[:500],
                )
            )
            await db.commit()
    except Exception as exc:
        logger.debug("memory link write skipped", pilot=pilot, error=str(exc))


async def _grade_distill_links(
    session: Any, grade_after: datetime, rot_after: datetime, now: datetime
) -> int:
    """memory_distill_gate: a post-decision retrieval of the recorded
    learning proves the persist-worthy claim held; no retrieval by the
    rot horizon proves it did not; rows whose learning was never linked
    (still in the persist pipeline) wait."""
    from roboco.db.tables import MemoryLinkTable, MemoryRetrievalLogTable

    rows = await _unlabeled_rows(session, "memory_distill_gate", grade_after)
    if not rows:
        return 0
    session_ids = [str(r.session_id) for r in rows]
    links = await session.execute(
        select(MemoryLinkTable).where(
            MemoryLinkTable.pilot == "memory_distill_gate",
            MemoryLinkTable.session_id.in_(session_ids),
        )
    )
    sources_by_session: dict[str, list[str]] = {}
    for link in links.scalars():
        sources_by_session.setdefault(link.session_id, []).append(link.source)
    graded = 0
    for row in rows:
        session_id = str(row.session_id)
        sources = sources_by_session.get(session_id)
        if not sources:
            continue  # learning never persisted: nothing to grade yet
        retrieved = await session.execute(
            select(MemoryRetrievalLogTable.id)
            .where(
                MemoryRetrievalLogTable.source.in_(sources),
                MemoryRetrievalLogTable.retrieved_at > row.created_at,
            )
            .limit(1)
        )
        if retrieved.first() is not None:
            fate = "retrieved"
        elif now >= rot_after:
            fate = "never_retrieved"
        else:
            continue
        graded += await persist.record_question_outcomes(
            session,
            pilot="memory_distill_gate",
            session_id=session_id,
            fates={"gate": fate},
        )
    return graded


def _vault_fate(
    entry: tuple[str, datetime | None] | None,
    decision_at: datetime,
) -> str | None:
    """One vault draft's fate: approved and started proves the note
    warranted a task; cancelled proves it did not; still pending waits."""
    if entry is None:
        return None
    status_now, updated_at = entry
    moved = _moved_after(updated_at, decision_at)
    if status_now == "cancelled" and moved:
        return "draft_cancelled"
    if status_now in _DELIVERED and moved:
        return "draft_approved"
    return None


async def _vault_drafts(
    session: Any, session_ids: list[str]
) -> tuple[dict[str, list[str]], dict[str, tuple[str, datetime | None]]]:
    """Draft task refs per decision session, plus their task facts."""
    from roboco.db.tables import MemoryLinkTable

    links = await session.execute(
        select(MemoryLinkTable).where(
            MemoryLinkTable.pilot == "vault_prefilter",
            MemoryLinkTable.session_id.in_(session_ids),
        )
    )
    draft_ids: list[str] = []
    drafts_by_session: dict[str, list[str]] = {}
    for link in links.scalars():
        task_ref = link.source.removeprefix("task:")
        if task_ref == link.source:
            continue
        drafts_by_session.setdefault(link.session_id, []).append(task_ref)
        draft_ids.append(task_ref)
    return drafts_by_session, await _task_facts(session, draft_ids)


async def _grade_vault_links(
    session: Any, grade_after: datetime
) -> int:
    """vault_prefilter: the board-review draft created from the note is
    the truth - approved and started proves the note warranted a task; a
    cancelled draft proves it did not; still pending waits."""
    rows = await _unlabeled_rows(session, "vault_prefilter", grade_after)
    if not rows:
        return 0
    session_ids = [str(r.session_id) for r in rows]
    drafts_by_session, facts = await _vault_drafts(session, session_ids)
    if not drafts_by_session:
        return 0
    graded = 0
    for row in rows:
        fates: dict[str, str] = {}
        for task_ref in drafts_by_session.get(str(row.session_id)) or []:
            fate = _vault_fate(facts.get(task_ref), row.created_at)
            if fate:
                fates["gate"] = fate
        if not fates:
            continue
        graded += await persist.record_question_outcomes(
            session,
            pilot="vault_prefilter",
            session_id=str(row.session_id),
            fates=fates,
        )
    return graded


async def _grade_memory_links(
    session: Any, grade_after: datetime, rot_after: datetime, now: datetime
) -> int:
    """Wave 5 entry: grade both memory-family pilots through their links."""
    graded = await _grade_distill_links(session, grade_after, rot_after, now)
    graded += await _grade_vault_links(session, grade_after)
    return graded
