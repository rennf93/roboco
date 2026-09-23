"""Fire-and-forget persistence for Decisions verdicts.

Every pilot routes its verdict through ``log_action``; this module turns
those calls into rows in ``decision_log`` (migration 105) without ever
blocking, failing, or slowing a decision path:

- Appends to a bounded in-memory buffer (oldest dropped when full).
- A single background flush task drains the buffer in batches over its own
  short-lived DB session (shared-session discipline: never the caller's).
- Any failure drops the batch with a debug log and keeps going - the log
  is evidence, not a gate, so losing rows under a DB outage is the right
  degradation.

The Auditor's daily decisions-audit board cycle reads this table as the
baseline history: verdict distributions, mean confidence, action counts,
and spend per pilot, plus the mode/threshold calibration trail.
"""

from __future__ import annotations

import asyncio
from collections import deque
from datetime import UTC, datetime
from typing import Any

import structlog

from roboco.config import settings
from roboco.db.tables import DecisionLogTable

logger = structlog.get_logger(__name__)

# Bounded buffer: under a DB outage the oldest unflushed rows fall off
# rather than growing without bound. Evidence ages fast anyway.
_BUFFER_MAX = 2_000
_FLUSH_DELAY_S = 5.0
_BATCH_SIZE = 200

_pending: deque[dict[str, Any]] = deque(maxlen=_BUFFER_MAX)
_STATE: dict[str, Any] = {"flush_task": None}


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
    answers: dict[str, Any] = {}
    confidence: dict[str, Any] = {}
    if result is not None:
        for key, ans in getattr(result, "answers", {}).items():
            answers[key] = ans.verdict()
            confidence[key] = ans.confidence
    if not answers and verdict is not None:
        # No typed answers came back (defensive): record the caller's
        # verdict summary so the row still says what the pilot decided.
        answers["verdict"] = verdict
    _pending.append(
        {
            "created_at": datetime.now(UTC),
            "pilot": pilot[:60],
            "tier": getattr(result, "tier", None),
            "mode": mode,
            "session_id": (
                getattr(result, "session_id", None)[:280]
                if getattr(result, "session_id", None)
                else None
            ),
            "answers": answers or None,
            "confidence": confidence or None,
            "action": str(action)[:160] if action is not None else None,
            "cost": getattr(getattr(result, "usage", None), "cost", None),
        }
    )
    _ensure_flusher()


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
