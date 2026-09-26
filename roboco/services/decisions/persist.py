"""Fire-and-forget persistence for Decisions verdicts.

Every pilot routes its verdict through ``log_action``; this module turns
those calls into rows in ``decision_log`` (migration 105, corpus columns
in 106) without ever blocking, failing, or slowing a decision path:

- Appends to a bounded in-memory buffer (oldest dropped when full).
- A single background flush task drains the buffer in batches over its own
  short-lived DB session (shared-session discipline: never the caller's).
- Any failure drops the batch with a debug log and keeps going - the log
  is evidence, not a gate, so losing rows under a DB outage is the right
  degradation.

Each row carries the question inputs EXACTLY as sent on the wire (the
client stamps its post-cap state and question payload onto the result), so
a row plus a ground-truth outcome is one fine-tunable example for the Laya
checkpoint. ``record_outcome`` is the labeled-row writer: outcome
producers (e.g. the self-heal recurrence check) attach what actually
happened to every unlabeled row of one (pilot, session_id) subject.

The Auditor's daily decisions-audit board cycle reads this table as the
baseline history: verdict distributions, mean confidence, action counts,
and spend per pilot, plus the mode/threshold calibration trail.
"""

from __future__ import annotations

import asyncio
import json
from collections import deque
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import update

from roboco.config import settings
from roboco.db.tables import DecisionLogTable

logger = structlog.get_logger(__name__)

# Bounded buffer: under a DB outage the oldest unflushed rows fall off
# rather than growing without bound. Evidence ages fast anyway.
_BUFFER_MAX = 2_000
_FLUSH_DELAY_S = 5.0
_BATCH_SIZE = 200

# Defensive serialized-size cap per corpus input (state, questions). The
# client's own caps bound the laya tier near 2k chars and the fallback
# tier's per-key caps near 8k, so this only bites a future call path that
# skips the client; the payload is dropped rather than stored oversize
# (a marked stub keeps the row honest about why inputs are missing).
_CORPUS_INPUT_CAP_CHARS = 64_000

_pending: deque[dict[str, Any]] = deque(maxlen=_BUFFER_MAX)
_STATE: dict[str, Any] = {"flush_task": None}


def _corpus_input(value: Any) -> dict[str, Any] | None:
    """Size-guard one corpus input for the JSON column. Returns the value
    verbatim under the cap, a marked stub over it, ``None`` for missing.
    Catches EVERY serialization failure: a poison input must drop the
    input, never the whole row (log_action's outer guard would otherwise
    skip the row entirely, losing the verdict too)."""
    if value is None:
        return None
    try:
        serialized = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return {"_dropped": "unserializable"}
    if len(serialized) > _CORPUS_INPUT_CAP_CHARS:
        return {"_dropped": f"oversize>{_CORPUS_INPUT_CAP_CHARS}"}
    return value


def _answers_from(result: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """(verdicts, confidences) keyed per question off a parsed result."""
    answers: dict[str, Any] = {}
    confidence: dict[str, Any] = {}
    if result is not None:
        for key, ans in getattr(result, "answers", {}).items():
            answers[key] = ans.verdict()
            confidence[key] = ans.confidence
    return answers, confidence


def record_decision(
    *,
    pilot: str,
    mode: str,
    verdict: Any,
    action: str | None,
    result: Any,
) -> None:
    """Buffer one decision row and make sure a flusher is scheduled.

    Sync and non-blocking: safe to call from log_action on any path. Never
    raises; when the master flag is off there is nothing to persist.
    """
    if not settings.decisions_enabled:
        return
    answers, confidence = _answers_from(result)
    if not answers and verdict is not None:
        # No typed answers came back (defensive): record the caller's
        # verdict summary so the row still says what the pilot decided.
        answers["verdict"] = verdict
    result_session_id: Any = getattr(result, "session_id", None)
    _pending.append(
        {
            "created_at": datetime.now(UTC),
            "pilot": pilot[:60],
            "tier": getattr(result, "tier", None),
            "mode": mode,
            "session_id": result_session_id[:280] if result_session_id else None,
            "answers": answers or None,
            "confidence": confidence or None,
            "state": _corpus_input(getattr(result, "state", None)),
            "questions": _corpus_input(getattr(result, "questions", None)),
            "action": str(action)[:160] if action is not None else None,
            "cost": getattr(getattr(result, "usage", None), "cost", None),
        }
    )
    _ensure_flusher()


def _clean_fates(fates: dict[str, str]) -> dict[str, str]:
    """Length- and emptiness-guarded copy of a per-question fate map."""
    return {
        str(k)[:60]: str(v)[:60] for k, v in fates.items() if str(v).strip()
    }


def _subject_update(pilot: str, session_id: str) -> Any:
    """The unlabeled-rows UPDATE target for one (pilot, session_id)."""
    return update(DecisionLogTable).where(
        DecisionLogTable.pilot == pilot[:60],
        DecisionLogTable.session_id == session_id[:280],
    )


async def record_outcome(
    session: Any,
    *,
    pilot: str,
    session_id: str,
    outcome: str,
    at: datetime | None = None,
    older_than: datetime | None = None,
) -> int:
    """Attach a ground-truth label to every unlabeled row of one subject.

    Outcome producers call this once the real-world result is known (e.g.
    the self-heal engine after its recurrence window closes: the gated
    breach either recurred or it did not). Labels EVERY unlabeled row
    matching (pilot, session_id): re-gates of the same subject share the
    outcome. ``older_than`` bounds the labels to rows created before that
    timestamp (the parking repark stamp uses it to grade only PRIOR
    parks, never the fresh one that triggered the stamp). Labeled rows
    are never overwritten. Returns the number of rows labeled; 0 when
    nothing matched (nothing to label is normal, not an error). Never
    raises: the caller's own path must keep working, so under any failure
    the label is simply missing and the row stays a corpus candidate.
    Runs its UPDATE inside a savepoint per the shared-session discipline,
    so a failure cannot poison the caller's transaction.
    """
    if not settings.decisions_enabled:
        return 0
    slug = outcome.strip()[:60]
    if not slug or not session_id:
        logger.warning(
            "decision outcome rejected",
            pilot=pilot,
            session_id=session_id,
            outcome=outcome,
        )
        return 0
    try:
        async with session.begin_nested():
            stmt = _subject_update(pilot, session_id).where(
                DecisionLogTable.outcome.is_(None)
            )
            if older_than is not None:
                stmt = stmt.where(DecisionLogTable.created_at < older_than)
            result = await session.execute(
                stmt.values(outcome=slug, outcome_at=at or datetime.now(UTC))
            )
        return int(result.rowcount or 0)
    except Exception as exc:
        logger.warning(
            "decision outcome labeling failed; rows stay unlabeled",
            pilot=pilot,
            session_id=session_id,
            error=str(exc),
        )
        return 0


def _ensure_flusher() -> None:
    """Schedule the drain task on the running loop (no-op without one, e.g.
    in sync test bodies - buffered rows stay pending for an async flush)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if _STATE["flush_task"] is not None and not _STATE["flush_task"].done():
        return
    _STATE["flush_task"] = loop.create_task(_drain())


async def _drain() -> None:
    """Flush the buffer after a short delay so bursts batch together."""
    await asyncio.sleep(_FLUSH_DELAY_S)
    while _pending:
        batch: list[dict[str, Any]] = []
        while _pending and len(batch) < _BATCH_SIZE:
            batch.append(_pending.popleft())
        try:
            await _write(batch)
        except Exception as exc:  # evidence, not a gate
            logger.debug(
                "decision_log flush failed; batch dropped",
                dropped=len(batch),
                error=str(exc),
            )


async def _write(batch: list[dict[str, Any]]) -> None:
    from roboco.db.base import get_session_factory

    factory = get_session_factory()
    async with factory() as db:
        db.add_all([DecisionLogTable(**row) for row in batch])
        await db.commit()


async def record_question_outcomes(
    session: Any,
    *,
    pilot: str,
    session_id: str,
    fates: dict[str, str],
    at: datetime | None = None,
) -> int:
    """Attach per-question fates to every unlabeled row of one subject.

    The batched-pilot counterpart of ``record_outcome`` (spec 12.1): a
    findings-mapping row's questions each carry their own truth, so the
    label is a {question_key: fate} map stored in ``question_outcomes``.
    Rows already carrying question outcomes are never overwritten. Same
    posture as ``record_outcome``: savepoint-wrapped, never raises, 0 on
    any failure or empty input.
    """
    if not settings.decisions_enabled or not fates:
        return 0
    clean = _clean_fates(fates)
    if not clean or not session_id:
        logger.warning(
            "decision question outcomes rejected",
            pilot=pilot,
            session_id=session_id,
            fates=len(clean),
        )
        return 0
    try:
        async with session.begin_nested():
            result = await session.execute(
                _subject_update(pilot, session_id)
                .where(DecisionLogTable.question_outcomes.is_(None))
                .values(
                    question_outcomes={
                        **clean,
                        "_labeled_at": (at or datetime.now(UTC)).isoformat(),
                    }
                )
            )
        return int(result.rowcount or 0)
    except Exception as exc:
        logger.warning(
            "decision question-outcome labeling failed; rows stay unlabeled",
            pilot=pilot,
            session_id=session_id,
            error=str(exc),
        )
        return 0


async def flush_now() -> int:
    """Test/maintenance hook: drain the buffer immediately. Returns the
    number of rows written; failures drop the batch, never raise."""
    count = 0
    while _pending:
        batch: list[dict[str, Any]] = []
        while _pending and len(batch) < _BATCH_SIZE:
            batch.append(_pending.popleft())
        try:
            await _write(batch)
            count += len(batch)
        except Exception as exc:  # evidence, not a gate
            logger.debug(
                "decision_log flush failed; batch dropped",
                dropped=len(batch),
                error=str(exc),
            )
    return count


def reset_persist_state() -> None:
    """Test hook: drop buffered rows and the flush task reference."""
    _pending.clear()
    _STATE["flush_task"] = None
