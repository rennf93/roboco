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
        del task_id
        return []
