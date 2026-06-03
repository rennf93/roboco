"""EvidenceRepo: aggregates unread A2As, mentions, notifications, etc.

Phase 1 stub returns empty lists; Phase 2+ wires real queries.
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
        del agent_id
        return []

    async def list_unread_mentions(self, agent_id: UUID) -> list[dict[str, Any]]:
        del agent_id
        return []

    async def list_pending_notifications(self, agent_id: UUID) -> list[dict[str, Any]]:
        del agent_id
        return []

    async def task_metadata_gaps(self, task_id: UUID) -> list[str]:
        del task_id
        return []

    async def recent_team_activity(self, agent_id: UUID) -> list[dict[str, Any]]:
        del agent_id
        return []

    async def blockers_in_lane(self, agent_id: UUID) -> list[dict[str, Any]]:
        del agent_id
        return []

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
