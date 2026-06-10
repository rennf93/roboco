"""EvidenceRepo: aggregates unread A2As, mentions, notifications, and task/team
context for an agent's ``context_briefing`` (plus the task journal handoff).

Each method is a single capped query over the live tables — they run on the
briefing-assembly path (per verb), so each stays cheap (LIMIT 10, indexed lookups).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession


class EvidenceRepo:
    def __init__(self, db_session: AsyncSession) -> None:
        self._db = db_session

    async def list_unread_a2a(self, agent_id: UUID) -> list[dict[str, Any]]:
        """Open A2A conversations with unread messages for this agent.

        Conversations are keyed by agent slug (``agent_a``/``agent_b``) with a
        per-side unread counter; surface the ones this agent has yet to read.
        """
        from sqlalchemy import or_, select

        from roboco.db.tables import A2AConversationTable, AgentTable

        slug = await self._db.scalar(
            select(AgentTable.slug).where(AgentTable.id == agent_id)
        )
        if slug is None:
            return []
        rows = (
            (
                await self._db.execute(
                    select(A2AConversationTable)
                    .where(
                        or_(
                            (A2AConversationTable.agent_a == slug)
                            & (A2AConversationTable.unread_by_a > 0),
                            (A2AConversationTable.agent_b == slug)
                            & (A2AConversationTable.unread_by_b > 0),
                        )
                    )
                    .order_by(A2AConversationTable.updated_at.desc())
                    .limit(10)
                )
            )
            .scalars()
            .all()
        )
        items: list[dict[str, Any]] = []
        for c in rows:
            is_a = c.agent_a == slug
            items.append(
                {
                    "conversation_id": str(c.id),
                    "from_agent": c.agent_b if is_a else c.agent_a,
                    "unread": c.unread_by_a if is_a else c.unread_by_b,
                    "topic": c.topic,
                    "task_id": str(c.task_id) if c.task_id else None,
                }
            )
        return items

    async def list_unread_mentions(self, agent_id: UUID) -> list[dict[str, Any]]:
        """Unacknowledged @mention notifications for this agent.

        Each channel @mention raises a MENTION-type notification (see
        ``messaging._notify_mentions``); surface the ones this agent has not yet
        acked. The agent clears them with ``notify_ack`` — so ``i_am_idle``'s
        mention soft-block is satisfiable rather than a permanent dead-end.
        (Channel messages carry no per-recipient read state of their own, so the
        notification's ``acked_by`` is the read signal.)
        """
        from sqlalchemy import select

        from roboco.db.tables import NotificationTable
        from roboco.models import NotificationType

        result = await self._db.execute(
            select(
                NotificationTable.id,
                NotificationTable.from_agent,
                NotificationTable.subject,
                NotificationTable.body,
                NotificationTable.related_task_id,
                NotificationTable.timestamp,
            )
            .where(NotificationTable.type == NotificationType.MENTION)
            .where(NotificationTable.to_agents.contains([agent_id]))
            .where(~NotificationTable.acked_by.contains([agent_id]))
            .order_by(NotificationTable.timestamp.desc())
            .limit(10)
        )
        return [
            {
                "notification_id": str(row.id),
                "from_agent": str(row.from_agent) if row.from_agent else None,
                "subject": row.subject,
                "excerpt": (row.body or "")[:280],
                "task_id": (str(row.related_task_id) if row.related_task_id else None),
                "timestamp": row.timestamp.isoformat() if row.timestamp else None,
            }
            for row in result.all()
        ]

    async def list_pending_notifications(self, agent_id: UUID) -> list[dict[str, Any]]:
        """Unacknowledged, unexpired notifications addressed to this agent."""
        from datetime import UTC, datetime

        from sqlalchemy import or_, select

        from roboco.db.tables import NotificationTable

        now = datetime.now(UTC)
        result = await self._db.execute(
            select(
                NotificationTable.id,
                NotificationTable.type,
                NotificationTable.priority,
                NotificationTable.subject,
                NotificationTable.body,
                NotificationTable.from_agent,
                NotificationTable.related_task_id,
                NotificationTable.timestamp,
            )
            .where(NotificationTable.to_agents.contains([agent_id]))
            .where(~NotificationTable.acked_by.contains([agent_id]))
            .where(
                or_(
                    NotificationTable.expires_at.is_(None),
                    NotificationTable.expires_at > now,
                )
            )
            .order_by(NotificationTable.timestamp.desc())
            .limit(10)
        )
        return [
            {
                "notification_id": str(row.id),
                "type": str(row.type),
                "priority": str(row.priority),
                "subject": row.subject,
                "body": row.body,
                "from_agent": str(row.from_agent) if row.from_agent else None,
                "task_id": str(row.related_task_id) if row.related_task_id else None,
                "timestamp": row.timestamp.isoformat() if row.timestamp else None,
            }
            for row in result.all()
        ]

    async def task_metadata_gaps(self, task_id: UUID) -> list[str]:
        """Human-readable gaps in a task's metadata the owner should fill."""
        from sqlalchemy import select

        from roboco.db.tables import TaskTable

        task = await self._db.scalar(select(TaskTable).where(TaskTable.id == task_id))
        if task is None:
            return []
        gaps: list[str] = []
        if not task.acceptance_criteria:
            gaps.append("no acceptance criteria")
        if not task.description:
            gaps.append("no description")
        return gaps

    async def recent_team_activity(self, agent_id: UUID) -> list[dict[str, Any]]:
        """Recently-updated tasks in this agent's team (lane awareness)."""
        from sqlalchemy import func, select

        from roboco.db.tables import AgentTable, TaskTable

        team = await self._db.scalar(
            select(AgentTable.team).where(AgentTable.id == agent_id)
        )
        if team is None:
            return []
        result = await self._db.execute(
            select(
                TaskTable.id,
                TaskTable.title,
                TaskTable.status,
                TaskTable.assigned_to,
                TaskTable.updated_at,
            )
            .where(TaskTable.team == team)
            .order_by(func.coalesce(TaskTable.updated_at, TaskTable.created_at).desc())
            .limit(10)
        )
        return [
            {
                "task_id": str(row.id),
                "title": row.title,
                "status": str(row.status),
                "assigned_to": str(row.assigned_to) if row.assigned_to else None,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            }
            for row in result.all()
        ]

    async def blockers_in_lane(self, agent_id: UUID) -> list[dict[str, Any]]:
        """Blocked tasks in this agent's team."""
        from sqlalchemy import select

        from roboco.db.tables import AgentTable, TaskTable
        from roboco.models.base import TaskStatus

        team = await self._db.scalar(
            select(AgentTable.team).where(AgentTable.id == agent_id)
        )
        if team is None:
            return []
        result = await self._db.execute(
            select(
                TaskTable.id,
                TaskTable.title,
                TaskTable.assigned_to,
                TaskTable.dependency_ids,
            )
            .where(TaskTable.team == team)
            .where(TaskTable.status == TaskStatus.BLOCKED)
            .order_by(TaskTable.updated_at.desc())
            .limit(10)
        )
        return [
            {
                "task_id": str(row.id),
                "title": row.title,
                "assigned_to": str(row.assigned_to) if row.assigned_to else None,
                "blocked_on": [str(d) for d in (row.dependency_ids or [])],
            }
            for row in result.all()
        ]

    async def journal_highlights_for_task(self, task_id: UUID) -> list[dict[str, Any]]:
        """The task's upstream handoff: every author's decision / reflection /
        note journal entry tied to this task, oldest first.

        This is what lets a downstream owner — e.g. the Main PM picking up a
        board-reviewed coordination task — read the Product Owner / Head of
        Marketing analysis instead of re-deriving it. Each row carries the
        author (slug + role) so the reader knows whose handoff it is. Learning
        and struggle entries are personal and excluded. Ownership is enforced by
        the caller (``evidence`` only serves the task's assignee), so private
        task-scoped entries are surfaced to the owner who needs the full handoff.
        """
        from sqlalchemy import select

        from roboco.db.tables import AgentTable, JournalEntryTable, JournalTable
        from roboco.models.base import JournalEntryType

        handoff_types = (
            JournalEntryType.DECISION_LOG,
            JournalEntryType.TASK_REFLECTION,
            JournalEntryType.GENERAL,
        )
        query = (
            select(
                JournalEntryTable.type,
                JournalEntryTable.title,
                JournalEntryTable.content,
                JournalEntryTable.timestamp,
                AgentTable.slug,
                AgentTable.role,
            )
            .join(JournalTable, JournalEntryTable.journal_id == JournalTable.id)
            .join(AgentTable, JournalTable.agent_id == AgentTable.id)
            .where(JournalEntryTable.task_id == task_id)
            .where(JournalEntryTable.type.in_(handoff_types))
            .order_by(JournalEntryTable.timestamp.asc())
            .limit(50)
        )
        result = await self._db.execute(query)
        return [
            {
                "author": row.slug,
                "author_role": str(row.role),
                "type": str(row.type),
                "title": row.title,
                "content": row.content,
                "timestamp": row.timestamp.isoformat() if row.timestamp else None,
            }
            for row in result.all()
        ]
