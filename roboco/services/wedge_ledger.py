"""Task-scoped wedge ledger: strike counting over the status-transition trail.

Generalizes the #685 per-unblock oscillation breaker to every status
transition regardless of verb or actor. The sync transition chokepoint
(``TaskService._emit_status_transition_audit``) appends one cycle entry per
hop onto the ``wedge_ledger`` marker (atomic with the transition) and
schedules :func:`evaluate_wedge_transition` here on a fresh session (the
coroner-bounce-hook pattern) — the fingerprint needs the async
terminal-children COUNT, which the sync chokepoint cannot run mid-transaction.

Policy (same #685 progress fingerprint — commit count, revision_count,
terminal children — never a second one):

* any fingerprint component MOVING clears the strike counter (real forward
  motion — commits, a QA/PM/CEO bounce, or a child going terminal);
* a transition into a status the task already held with the fingerprint
  static accrues one strike — status-keyed, not actor-keyed, so the
  September mixed-verb loop (unblock-OK → complete not_authorized →
  escalate bounce → pool reclaim) accumulates what the per-unblock counter
  missed;
* past ``settings.wedge_ledger_strike_threshold`` the task is force-BLOCKED
  for a human (HUMAN resolver, same shape as the oscillation breaker) and
  the CEO notification carries the full cycle — actors, verbs, timestamps —
  so the CEO sees the shape of the loop, not just that one exists.

The evaluator is idempotent per hop: it only processes cycle entries past
the persisted ``evaluated`` cursor, so a scheduled-but-not-yet-run
evaluation and the next transition's evaluation can never double-count.
"""

from __future__ import annotations

import uuid as uuid_mod
from typing import Any

import structlog

from roboco.foundation.policy.content import markers

logger = structlog.get_logger()


def _fingerprint_moved(prior: list[int] | None, current: list[int]) -> bool:
    """True when any of the three #685 fingerprint components changed."""
    return prior is not None and prior != current


def count_wedge_strikes(
    cycle: list[dict[str, Any]],
    start: int,
    *,
    strikes: int,
    progress_moved: bool,
) -> int:
    """Pure strike rule over the new cycle entries ``cycle[start:]``.

    With the fingerprint static, each entry whose status was already held
    earlier in the trail is one strike (a return to a previously-held
    status). Any fingerprint movement clears the counter before counting —
    the reset rule. First-ever evaluation (no prior fingerprint) only sets
    the baseline: the loop needed a reference window to be meaningful.
    """
    if progress_moved:
        return 0
    seen = {entry.get("status") for entry in cycle[:start]}
    for entry in cycle[start:]:
        status = entry.get("status")
        if status in seen:
            strikes += 1
        seen.add(status)
    return strikes


def render_wedge_cycle(cycle: list[dict[str, Any]]) -> str:
    """Render the full hop-by-hop cycle for the CEO notification — actors,
    verbs, timestamps, one line per hop, oldest first."""
    lines = []
    for entry in cycle:
        verb = entry.get("verb") or "direct-transition"
        role = entry.get("agent_role") or "system"
        actor = entry.get("agent_id") or "-"
        ts = entry.get("ts") or "-"
        lines.append(
            f"- {entry.get('status')} via {verb} (actor: {role}/{actor}, {ts})"
        )
    return "\n".join(lines)


async def evaluate_wedge_transition(task_id: uuid_mod.UUID) -> bool:
    """Evaluate the ledger for task ``task_id`` after new transition hops.

    Returns True when this call tripped the breaker and force-blocked the
    task. Never raises into the caller (the transition already happened);
    all failures are logged best-effort, mirroring the coroner hook.
    """
    from roboco.config import settings

    try:
        return await _evaluate(task_id, settings.wedge_ledger_strike_threshold)
    except Exception:
        logger.warning(
            "wedge ledger evaluation failed (best-effort)", task_id=str(task_id)
        )
        return False


async def _evaluate(task_id: uuid_mod.UUID, threshold: int) -> bool:
    from roboco.db.base import get_db_context
    from roboco.db.tables import TaskTable
    from roboco.services.task import TaskService

    async with get_db_context() as db:
        task = await db.get(TaskTable, task_id)
        if task is None:
            return False
        ledger = markers.get_wedge_ledger(task)
        if ledger["tripped"]:
            return False
        cycle = ledger["cycle"]
        start = min(ledger["evaluated"], len(cycle))
        if start >= len(cycle):
            return False

        terminal_children = await TaskService(db).terminal_children_count(task_id)
        progress_fp = [
            len(task.commits or []),
            int(task.revision_count or 0),
            terminal_children,
        ]
        moved = _fingerprint_moved(ledger["progress_fp"], progress_fp)
        strikes = count_wedge_strikes(
            cycle,
            start,
            strikes=0 if moved or ledger["progress_fp"] is None else ledger["strikes"],
            progress_moved=moved,
        )
        markers.set_wedge_strike_state(task, strikes=strikes, progress_fp=progress_fp)
        state = markers.get_wedge_ledger(task)
        state["evaluated"] = len(cycle)
        markers.set_marker(task, markers.WEDGE_LEDGER, state)
        await db.commit()

        if strikes < threshold:
            return False
        return await _block_for_human(db, task, strikes)


async def _block_for_human(db: Any, task: Any, strikes: int) -> bool:
    from roboco.models.base import BlockerResolverType, TaskStatus
    from roboco.services.notification import NotificationService
    from roboco.services.task import TaskService

    cycle = markers.get_wedge_ledger(task)["cycle"]
    try:
        task.blocker_resolver_type = BlockerResolverType.HUMAN
        markers.mark_wedge_tripped(task)
        await db.commit()
        await TaskService(db).admin_set_status(
            task.id, TaskStatus.BLOCKED, actor_role="system"
        )
        await db.commit()
    except Exception:
        logger.warning(
            "wedge ledger force-block failed (best-effort)",
            task_id=str(task.id),
            strikes=strikes,
        )
        return False
    try:
        await NotificationService().send_wedge_blocked_notification(
            task_id=str(task.id),
            strikes=strikes,
            cycle=render_wedge_cycle(cycle),
            task_title=task.title,
            db_session=db,
        )
        await db.commit()
    except Exception:
        logger.warning(
            "wedge ledger CEO notification failed (best-effort)",
            task_id=str(task.id),
            strikes=strikes,
        )
    return True
