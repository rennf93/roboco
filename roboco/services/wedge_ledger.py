"""Task-scoped wedge ledger.

Generalizes the #685 oscillation breaker (which counts strikes per
``unblock`` only) to EVERY status transition, regardless of actor or verb.
The September mixed-verb loop — unblock succeeds, the next verb bounces
``not_authorized``, an escalation blocks the task, the pool reclaims it,
respawn — accumulated zero strikes on any single verb; a task cycling back
to the same status without real progress is wedged no matter who drives the
round trips, so counting is STATUS-keyed here, not actor-keyed.

Movement is judged by the SAME progress fingerprint the #685 breaker uses —
``[commit_count, revision_count, terminal_children]`` — extended task-scoped
and verb-agnostic; no second fingerprint exists. Any of the three signals
moving clears every counter. Past ``settings.wedge_ledger_threshold``
(default 5, aligned with the gateway's ``_OSCILLATION_TRIP_THRESHOLD``) the
task force-blocks for a human (``blocker_resolver_type=HUMAN``) and the CEO
receives the full transition cycle — actor, role, transition, timestamp —
rebuilt from the audit log, so the shape of the loop is visible, not just
its existence.

All ledger logic and DB writes live here; callers (the transition
chokepoint in ``TaskService``) only schedule the check.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import structlog
from sqlalchemy import select

from roboco.foundation.policy.content import markers

if TYPE_CHECKING:
    from uuid import UUID

logger = structlog.get_logger(__name__)

# Audit rows to include in the CEO cycle payload — a wedged loop is a
# handful of transitions; 20 covers any real loop with headroom.
_CYCLE_LIMIT = 20


def progress_fingerprint(
    commit_count: int, revision_count: int, terminal_children: int
) -> list[int]:
    """The #685 progress fingerprint, verbatim.

    Commit count + revision_count are already loaded on the task row; the
    terminal-children COUNT exists because a PM coordination root never
    commits itself — child completions are its only progress signal.
    """
    return [int(commit_count), int(revision_count), int(terminal_children)]


def evaluate_arrival(
    ledger: dict[str, Any], to_status: str, fp: list[int]
) -> dict[str, Any]:
    """Pure: fold one status arrival into the ledger; returns the updated
    ledger dict.

    A fingerprint that differs from the last recorded one is real forward
    motion: every strike counter clears (the reset rule). Otherwise — the
    first-ever fingerprint (no prior recorded, the #685 accrue-on-first
    semantics) or an unchanged one — the task arrived at a status with
    nothing landing in between: one strike on that status, keyed by status
    alone so the count accrues across any mix of actors and verbs.
    """
    strikes: dict[str, int] = {
        s: int(n) for s, n in (ledger.get("strikes") or {}).items()
    }
    prior_fp = ledger.get("progress_fp")
    if prior_fp is not None and list(prior_fp) != fp:
        return {"progress_fp": fp, "strikes": {}, "tripped": False}
    strikes[to_status] = strikes.get(to_status, 0) + 1
    return {
        "progress_fp": fp,
        "strikes": strikes,
        "tripped": bool(ledger.get("tripped")),
    }


def max_strikes(ledger: dict[str, Any]) -> int:
    return max((int(n) for n in (ledger.get("strikes") or {}).values()), default=0)


def get_wedge_state(task: Any) -> dict[str, Any]:
    """Read-side helper (be-dev-1's stuck-state payload reads this): the
    ledger marker with guaranteed keys — ``strikes`` always a dict,
    ``tripped`` always a bool."""
    ledger = markers.get_wedge_ledger(task)
    return {
        "progress_fp": list(ledger.get("progress_fp") or []),
        "strikes": dict(ledger.get("strikes") or {}),
        "tripped": bool(ledger.get("tripped")),
        "tripped_at": ledger.get("tripped_at"),
        "tripped_strikes": int(ledger.get("tripped_strikes") or 0),
    }


class WedgeLedgerService:
    """Evaluates one status arrival against the ledger and trips the
    breaker past the threshold. Constructed per-check on a fresh session
    (the check runs as a scheduled background task)."""

    def __init__(self, session: Any) -> None:
        self.session = session

    async def check_transition(
        self, task_id: UUID, to_status: str
    ) -> dict[str, Any] | None:
        """Fold one arrival; returns a trip payload when the threshold was
        crossed (and the task was force-blocked), else ``None``. Never
        raises into the caller — the transition already happened either way.

        Arrivals at BLOCKED and the terminal statuses are not counted: the
        trip itself transitions into BLOCKED (recursion guard), and a
        terminal task is done regardless of its ledger history.
        """
        from roboco.models.base import TaskStatus

        if to_status in (
            TaskStatus.BLOCKED.value,
            TaskStatus.COMPLETED.value,
            TaskStatus.CANCELLED.value,
        ):
            return None
        task = await self.session.get(_task_table(), task_id)
        if task is None:
            return None

        fp = progress_fingerprint(
            len(task.commits or []),
            int(task.revision_count or 0),
            await _terminal_children_count(self.session, task_id),
        )
        ledger = evaluate_arrival(markers.get_wedge_ledger(task), to_status, fp)
        markers.set_wedge_ledger(task, ledger)
        await self.session.commit()

        from roboco.config import settings

        threshold = settings.wedge_ledger_threshold
        if ledger["tripped"] or max_strikes(ledger) <= threshold:
            return None
        return await self._trip(task, to_status, ledger, threshold)

    async def _trip(
        self, task: Any, to_status: str, ledger: dict[str, Any], threshold: int
    ) -> dict[str, Any]:
        """Force-block for a human and notify the CEO with the full cycle."""
        from roboco.models.base import BlockerResolverType, TaskStatus

        cycle = await self._transition_cycle(task.id)
        now = datetime.now(UTC).isoformat()
        ledger["tripped"] = True
        ledger["tripped_at"] = now
        ledger["tripped_strikes"] = max_strikes(ledger)
        markers.set_wedge_ledger(task, ledger)
        task.blocker_resolver_type = BlockerResolverType.HUMAN
        await self.session.commit()

        from roboco.services.task import TaskService

        await TaskService(self.session).admin_set_status(
            task.id, TaskStatus.BLOCKED, actor_role="system"
        )
        await self.session.commit()
        try:
            from roboco.services.notification import NotificationService

            await NotificationService().send_wedge_blocked_notification(
                task_id=str(task.id),
                strikes=max_strikes(ledger),
                status=to_status,
                cycle=cycle,
                db_session=self.session,
                task_title=task.title,
            )
        except Exception:
            logger.warning(
                "failed to send CEO wedge-blocked notification",
                task_id=str(task.id),
            )
        logger.warning(
            "wedge ledger tripped: task force-blocked for a human",
            task_id=str(task.id),
            strikes=max_strikes(ledger),
            threshold=threshold,
        )
        return {
            "task_id": str(task.id),
            "strikes": max_strikes(ledger),
            "tripped_at": now,
            "cycle": cycle,
        }

    async def _transition_cycle(self, task_id: UUID) -> list[dict[str, Any]]:
        """The full transition cycle — actor, role, transition, timestamp —
        rebuilt from the audit log's ``task.<status>`` rows (oldest last).
        The audit journey is already the source of truth; the ledger never
        duplicates it."""
        from roboco.db.tables import AuditLogTable

        result = await self.session.execute(
            select(AuditLogTable)
            .where(AuditLogTable.event_type.like("task.%"))
            .where(AuditLogTable.target_type == "task")
            .where(AuditLogTable.target_id == task_id)
            .order_by(AuditLogTable.timestamp.desc())
            .limit(_CYCLE_LIMIT)
        )
        return [
            {
                "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                "event": row.event_type,
                "actor": str(row.agent_id) if row.agent_id else None,
                "actor_role": (row.details or {}).get("agent_role"),
                "from_status": (row.details or {}).get("from_status"),
                "to_status": (row.details or {}).get("to_status"),
            }
            for row in reversed(result.scalars().all())
        ]


def _task_table() -> Any:
    from roboco.db.tables import TaskTable

    return TaskTable


async def _terminal_children_count(session: Any, task_id: UUID) -> int:
    """Terminal-children COUNT via the owning service, so the fingerprint's
    third component has exactly one definition."""
    from roboco.services.task import TaskService

    return await TaskService(session).terminal_children_count(task_id)


async def run_wedge_ledger_check(task_id: UUID, to_status: str) -> None:
    """Best-effort entrypoint scheduled from the transition chokepoint —
    opens its OWN fresh DB session rather than reusing the caller's
    mid-transaction one (the caller's transaction may still roll back; the
    same accepted small race the coroner bounce hook carries)."""
    from roboco.db.base import get_db_context

    try:
        async with get_db_context() as db:
            await WedgeLedgerService(db).check_transition(task_id, to_status)
    except Exception:
        logger.warning(
            "wedge ledger check failed (best-effort)",
            task_id=str(task_id),
            to_status=to_status,
        )
