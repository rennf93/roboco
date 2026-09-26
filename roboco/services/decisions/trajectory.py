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

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select

from roboco.config import settings
from roboco.db.tables import DecisionLogTable, TaskTable
from roboco.services.decisions import persist
from roboco.services.decisions.outcomes import (
    IDLE_WAIT_RESOLVED_AFTER,
    OWNED_TASK_ROTTED_AFTER,
    OWNED_TASK_SUBMITTED_AFTER,
    PLAN_BOUNCED_AFTER,
    PLAN_STALLED_PAST_WINDOW,
    QA_PASSED_AFTER,
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
)


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
