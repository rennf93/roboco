"""Recipient-context composer for the cognition lane (spec sections 4, 6.6).

Assembles an agent's work context (current task, siblings, parked work,
spawn age, journal) from tables the orchestrator already holds; feeds every
cognition-lane verdict. The composer NEVER asks the recipient anything: the
orchestrator holds every field. Every leg is best-effort and fail-open - a
missing field is simply omitted, because the verdict only modulates
delivery, never gates it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog
from sqlalchemy import func, select

from roboco.db.tables import (
    AgentTable,
    JournalEntryTable,
    JournalTable,
    TaskTable,
    WorkSessionTable,
)
from roboco.models.base import TaskStatus

logger = structlog.get_logger(__name__)

# Sibling statuses that still count as "in the queue" for sequence position.
_NON_TERMINAL = tuple(
    s
    for s in TaskStatus
    if s
    not in (
        TaskStatus.COMPLETED,
        TaskStatus.CANCELLED,
        TaskStatus.BACKLOG,
    )
)

# "Other claimed or parked" work: tasks the recipient holds beyond the
# current one (spec 6.6 recipient work context).
_OTHER_ACTIVE_STATUSES = (
    TaskStatus.CLAIMED,
    TaskStatus.PAUSED,
    TaskStatus.PENDING,
    TaskStatus.BLOCKED,
)

_OTHER_TASK_TITLE_CAP = 5


async def recipient_work_context(session, recipient_slug: str) -> dict:
    """Compose the recipient's work context for a steer-gate verdict.

    Returns a flat JSON-safe dict; on any internal failure the affected
    fields are simply absent (fail-open: the gate still runs on what it
    has, and a Jev-less fallback is today's behavior anyway).
    """
    context: dict = {"recipient": recipient_slug}
    try:
        agent = (
            await session.execute(
                select(AgentTable).where(AgentTable.slug == recipient_slug)
            )
        ).scalar_one_or_none()
    except Exception as exc:
        logger.warning(
            "recipient-context composer could not load the agent row",
            recipient=recipient_slug,
            error=str(exc),
        )
        return context
    if agent is None:
        return context

    current_task = None
    if agent.current_task_id is not None:
        current_task = (
            await session.execute(
                select(TaskTable).where(TaskTable.id == agent.current_task_id)
            )
        ).scalar_one_or_none()
    if current_task is not None:
        context["current_task_id"] = str(current_task.id)
        context["current_task_title"] = current_task.title
        context["current_task_status"] = (
            current_task.status.value
            if hasattr(current_task.status, "value")
            else str(current_task.status)
        )
        context["blocked_on"] = current_task.stalled_reason
        context["sequence_position_among_siblings"] = await _sequence_position(
            session, current_task
        )
        context["spawn_age_minutes"] = await _spawn_age_minutes(
            session, agent.id, current_task.id
        )

    others = (
        (
            await session.execute(
                select(TaskTable)
                .where(
                    TaskTable.assigned_to == agent.id,
                    TaskTable.status.in_(_OTHER_ACTIVE_STATUSES),
                    TaskTable.id != agent.current_task_id,
                )
                .order_by(TaskTable.sequence)
                .limit(_OTHER_TASK_TITLE_CAP + 1)
            )
        )
        .scalars()
        .all()
    )
    context["other_claimed_or_parked_tasks"] = {
        "count": len(others),
        "titles": [t.title for t in others[:_OTHER_TASK_TITLE_CAP]],
    }

    last_scope = await _last_journal_scope(session, agent.id)
    if last_scope is not None:
        context["last_journal_scope"] = last_scope
    return context


async def _sequence_position(session, task: TaskTable) -> int | None:
    """The task's 1-based position among its non-terminal same-parent
    siblings (lower sequence = earlier in the queue)."""
    query = (
        select(func.count())
        .select_from(TaskTable)
        .where(
            TaskTable.parent_task_id == task.parent_task_id,
            TaskTable.status.in_(_NON_TERMINAL),
            TaskTable.sequence < task.sequence,
        )
    )
    try:
        earlier = (await session.execute(query)).scalar_one()
        return int(earlier) + 1
    except Exception as exc:
        logger.debug("sequence position unavailable", error=str(exc))
        return None


async def _spawn_age_minutes(session, agent_id, task_id) -> float | None:
    """Age of the recipient's active work session on the current task, in
    minutes (best available proxy for spawn age without the runtime
    registry)."""
    try:
        row = (
            await session.execute(
                select(WorkSessionTable)
                .where(
                    WorkSessionTable.agent_id == agent_id,
                    WorkSessionTable.task_id == task_id,
                    WorkSessionTable.status == "active",
                )
                .order_by(WorkSessionTable.started_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    except Exception as exc:
        logger.debug("spawn age unavailable", error=str(exc))
        return None
    if row is None:
        return None
    started = row.started_at
    if started is None:
        return None
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    return round((datetime.now(UTC) - started).total_seconds() / 60, 1)


async def _last_journal_scope(session, agent_id) -> str | None:
    """A one-line scope of the recipient's most recent journal entry."""
    try:
        row = (
            await session.execute(
                select(JournalEntryTable)
                .join(JournalTable, JournalEntryTable.journal_id == JournalTable.id)
                .where(JournalTable.agent_id == agent_id)
                .order_by(JournalEntryTable.timestamp.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
    except Exception as exc:
        logger.debug("last journal scope unavailable", error=str(exc))
        return None
    if row is None:
        return None
    return f"{row.type.value if hasattr(row.type, 'value') else row.type}: {row.title}"
