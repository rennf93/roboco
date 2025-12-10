"""
Journal API Service

Manages agent personal journals for reflection, growth tracking, and debugging.
Each agent has their own journal with entries tied to tasks and sessions.
Integrates with the Optimal API for RAG indexing of entries.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.db.tables import JournalEntryTable, JournalTable
from roboco.models.base import JournalEntryType
from roboco.models.journal import (
    Journal,
    JournalEntry,
    JournalEntryCreate,
    create_decision_log,
    create_general_entry,
    create_learning_entry,
    create_struggle_entry,
    create_task_reflection,
)

logger = structlog.get_logger()


@dataclass
class JournalStats:
    """Statistics for a journal."""

    total_entries: int
    entries_by_type: dict[str, int]
    last_entry_at: datetime | None
    has_summary: bool


@dataclass
class GrowthMetrics:
    """Growth metrics calculated from journal entries."""

    total_reflections: int
    total_learnings: int
    total_struggles: int
    total_decisions: int
    struggle_resolution_rate: float  # Percentage of struggles that were resolved
    learning_frequency: float  # Average learnings per day
    sentiment_trend: str  # "improving", "stable", "declining"


class JournalService:
    """
    Service for managing agent journals.

    Provides CRUD operations for journals and entries,
    plus analytics and growth tracking.
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db
        self._optimal_service: Any = None  # Lazy loaded to avoid circular import

    async def _get_optimal_service(self) -> Any:
        """Lazy load the OptimalService."""
        if self._optimal_service is None:
            from roboco.services.optimal import get_optimal_service

            self._optimal_service = await get_optimal_service()
        return self._optimal_service

    # =========================================================================
    # JOURNAL CRUD
    # =========================================================================

    async def get_or_create_journal(self, agent_id: UUID) -> Journal:
        """
        Get or create a journal for an agent.

        Args:
            agent_id: The agent's UUID

        Returns:
            The agent's journal
        """
        result = await self._db.execute(
            select(JournalTable).where(JournalTable.agent_id == agent_id)
        )
        journal_row = result.scalar_one_or_none()

        if journal_row:
            return Journal(
                id=journal_row.id,
                agent_id=journal_row.agent_id,
                total_entries=journal_row.total_entries,
                last_entry_at=journal_row.last_entry_at,
                latest_summary=journal_row.latest_summary,
                summary_updated_at=journal_row.summary_updated_at,
                entries_by_type=journal_row.entries_by_type,
                created_at=journal_row.created_at,
                updated_at=journal_row.updated_at,
            )

        # Create new journal
        new_journal = JournalTable(agent_id=agent_id)
        self._db.add(new_journal)
        await self._db.commit()
        await self._db.refresh(new_journal)

        logger.info(
            "Created new journal",
            agent_id=str(agent_id),
            journal_id=str(new_journal.id),
        )

        return Journal(
            id=new_journal.id,
            agent_id=new_journal.agent_id,
            total_entries=0,
            created_at=new_journal.created_at,
        )

    async def get_journal(self, journal_id: UUID) -> Journal | None:
        """Get a journal by ID."""
        result = await self._db.execute(
            select(JournalTable).where(JournalTable.id == journal_id)
        )
        journal_row = result.scalar_one_or_none()

        if not journal_row:
            return None

        return Journal(
            id=journal_row.id,
            agent_id=journal_row.agent_id,
            total_entries=journal_row.total_entries,
            last_entry_at=journal_row.last_entry_at,
            latest_summary=journal_row.latest_summary,
            summary_updated_at=journal_row.summary_updated_at,
            entries_by_type=journal_row.entries_by_type,
            created_at=journal_row.created_at,
            updated_at=journal_row.updated_at,
        )

    async def get_journal_by_agent(self, agent_id: UUID) -> Journal | None:
        """Get a journal by agent ID."""
        result = await self._db.execute(
            select(JournalTable).where(JournalTable.agent_id == agent_id)
        )
        journal_row = result.scalar_one_or_none()

        if not journal_row:
            return None

        return Journal(
            id=journal_row.id,
            agent_id=journal_row.agent_id,
            total_entries=journal_row.total_entries,
            last_entry_at=journal_row.last_entry_at,
            latest_summary=journal_row.latest_summary,
            summary_updated_at=journal_row.summary_updated_at,
            entries_by_type=journal_row.entries_by_type,
            created_at=journal_row.created_at,
            updated_at=journal_row.updated_at,
        )

    # =========================================================================
    # ENTRY CRUD
    # =========================================================================

    async def create_entry(self, entry_create: JournalEntryCreate) -> JournalEntry:
        """
        Create a new journal entry.

        Args:
            entry_create: Entry creation schema

        Returns:
            The created entry
        """
        # Create entry in database
        entry_row = JournalEntryTable(
            journal_id=entry_create.journal_id,
            type=entry_create.type,
            title=entry_create.title,
            content=entry_create.content,
            task_id=entry_create.task_id,
            session_id=entry_create.session_id,
            tags=entry_create.tags,
            sentiment=entry_create.sentiment,
            is_private=entry_create.is_private,
        )
        self._db.add(entry_row)

        # Update journal metadata
        result = await self._db.execute(
            select(JournalTable).where(JournalTable.id == entry_create.journal_id)
        )
        journal_row = result.scalar_one_or_none()

        if journal_row:
            journal_row.total_entries += 1
            journal_row.last_entry_at = datetime.utcnow()
            type_key = (
                entry_create.type.value
                if isinstance(entry_create.type, JournalEntryType)
                else entry_create.type
            )
            entries_by_type = dict(journal_row.entries_by_type)
            entries_by_type[type_key] = entries_by_type.get(type_key, 0) + 1
            journal_row.entries_by_type = entries_by_type

        await self._db.commit()
        await self._db.refresh(entry_row)

        logger.info(
            "Created journal entry",
            entry_id=str(entry_row.id),
            journal_id=str(entry_create.journal_id),
            type=entry_create.type,
        )

        # Index in RAG - non-blocking
        try:
            optimal = await self._get_optimal_service()
            await optimal.index_journal_entry(
                entry_id=entry_row.id,
                agent_id=journal_row.agent_id
                if journal_row
                else entry_create.journal_id,
                content=entry_create.content,
                entry_type=type_key,
                task_id=entry_create.task_id,
                tags=entry_create.tags,
            )
        except Exception as e:
            logger.warning("Failed to index journal entry in RAG", error=str(e))

        return JournalEntry(
            id=entry_row.id,
            journal_id=entry_row.journal_id,
            type=entry_row.type,
            title=entry_row.title,
            content=entry_row.content,
            task_id=entry_row.task_id,
            session_id=entry_row.session_id,
            timestamp=entry_row.timestamp,
            tags=entry_row.tags,
            sentiment=entry_row.sentiment,
            is_private=entry_row.is_private,
            created_at=entry_row.created_at,
        )

    async def get_entry(self, entry_id: UUID) -> JournalEntry | None:
        """Get a journal entry by ID."""
        result = await self._db.execute(
            select(JournalEntryTable).where(JournalEntryTable.id == entry_id)
        )
        entry_row = result.scalar_one_or_none()

        if not entry_row:
            return None

        return JournalEntry(
            id=entry_row.id,
            journal_id=entry_row.journal_id,
            type=entry_row.type,
            title=entry_row.title,
            content=entry_row.content,
            task_id=entry_row.task_id,
            session_id=entry_row.session_id,
            timestamp=entry_row.timestamp,
            tags=entry_row.tags,
            sentiment=entry_row.sentiment,
            is_private=entry_row.is_private,
            created_at=entry_row.created_at,
            updated_at=entry_row.updated_at,
        )

    async def list_entries(
        self,
        journal_id: UUID,
        entry_type: JournalEntryType | None = None,
        task_id: UUID | None = None,
        limit: int = 50,
        offset: int = 0,
        include_private: bool = False,
    ) -> list[JournalEntry]:
        """
        List journal entries with filtering.

        Args:
            journal_id: Journal to list entries from
            entry_type: Filter by entry type
            task_id: Filter by related task
            limit: Maximum entries to return
            offset: Pagination offset
            include_private: Include private entries

        Returns:
            List of journal entries
        """
        query = select(JournalEntryTable).where(
            JournalEntryTable.journal_id == journal_id
        )

        if entry_type:
            query = query.where(JournalEntryTable.type == entry_type)

        if task_id:
            query = query.where(JournalEntryTable.task_id == task_id)

        if not include_private:
            query = query.where(JournalEntryTable.is_private == False)  # noqa: E712

        query = query.order_by(JournalEntryTable.timestamp.desc())
        query = query.limit(limit).offset(offset)

        result = await self._db.execute(query)
        rows = result.scalars().all()

        return [
            JournalEntry(
                id=row.id,
                journal_id=row.journal_id,
                type=row.type,
                title=row.title,
                content=row.content,
                task_id=row.task_id,
                session_id=row.session_id,
                timestamp=row.timestamp,
                tags=row.tags,
                sentiment=row.sentiment,
                is_private=row.is_private,
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    async def delete_entry(self, entry_id: UUID) -> bool:
        """Delete a journal entry."""
        result = await self._db.execute(
            select(JournalEntryTable).where(JournalEntryTable.id == entry_id)
        )
        entry_row = result.scalar_one_or_none()

        if not entry_row:
            return False

        # Update journal metadata
        journal_result = await self._db.execute(
            select(JournalTable).where(JournalTable.id == entry_row.journal_id)
        )
        journal_row = journal_result.scalar_one_or_none()

        if journal_row:
            journal_row.total_entries = max(0, journal_row.total_entries - 1)
            type_key = (
                entry_row.type.value
                if isinstance(entry_row.type, JournalEntryType)
                else entry_row.type
            )
            entries_by_type = dict(journal_row.entries_by_type)
            if type_key in entries_by_type:
                entries_by_type[type_key] = max(0, entries_by_type[type_key] - 1)
            journal_row.entries_by_type = entries_by_type

        await self._db.delete(entry_row)
        await self._db.commit()

        logger.info("Deleted journal entry", entry_id=str(entry_id))
        return True

    # =========================================================================
    # CONVENIENCE METHODS
    # =========================================================================

    async def add_task_reflection(
        self,
        agent_id: UUID,
        task_id: UUID,
        title: str,
        what_done: str,
        what_learned: str,
        what_struggled: str,
        next_steps: list[str],
        tags: list[str] | None = None,
    ) -> JournalEntry:
        """Add a task reflection entry."""
        journal = await self.get_or_create_journal(agent_id)
        entry = create_task_reflection(
            journal_id=journal.id,
            task_id=task_id,
            title=title,
            what_done=what_done,
            what_learned=what_learned,
            what_struggled=what_struggled,
            next_steps=next_steps,
            tags=tags,
        )
        return await self.create_entry(
            JournalEntryCreate(
                journal_id=entry.journal_id,
                type=entry.type,
                title=entry.title,
                content=entry.content,
                task_id=entry.task_id,
                tags=entry.tags,
            )
        )

    async def add_decision_log(
        self,
        agent_id: UUID,
        title: str,
        context: str,
        options: list[dict[str, str]],
        chosen: str,
        rationale: str,
        consequences: list[str],
        task_id: UUID | None = None,
        tags: list[str] | None = None,
    ) -> JournalEntry:
        """Add a decision log entry."""
        journal = await self.get_or_create_journal(agent_id)
        entry = create_decision_log(
            journal_id=journal.id,
            title=title,
            context=context,
            options=options,
            chosen=chosen,
            rationale=rationale,
            consequences=consequences,
            task_id=task_id,
            tags=tags,
        )
        return await self.create_entry(
            JournalEntryCreate(
                journal_id=entry.journal_id,
                type=entry.type,
                title=entry.title,
                content=entry.content,
                task_id=entry.task_id,
                tags=entry.tags,
            )
        )

    async def add_learning(
        self,
        agent_id: UUID,
        title: str,
        what_learned: str,
        how_applied: str | None = None,
        source: str | None = None,
        task_id: UUID | None = None,
        tags: list[str] | None = None,
    ) -> JournalEntry:
        """Add a learning entry."""
        journal = await self.get_or_create_journal(agent_id)
        entry = create_learning_entry(
            journal_id=journal.id,
            title=title,
            what_learned=what_learned,
            how_applied=how_applied,
            source=source,
            task_id=task_id,
            tags=tags,
        )
        return await self.create_entry(
            JournalEntryCreate(
                journal_id=entry.journal_id,
                type=entry.type,
                title=entry.title,
                content=entry.content,
                task_id=entry.task_id,
                tags=entry.tags,
                sentiment=entry.sentiment,
            )
        )

    async def add_struggle(
        self,
        agent_id: UUID,
        title: str,
        what_struggled: str,
        attempted_solutions: list[str],
        resolution: str | None = None,
        help_needed: str | None = None,
        task_id: UUID | None = None,
        tags: list[str] | None = None,
    ) -> JournalEntry:
        """Add a struggle entry."""
        journal = await self.get_or_create_journal(agent_id)
        entry = create_struggle_entry(
            journal_id=journal.id,
            title=title,
            what_struggled=what_struggled,
            attempted_solutions=attempted_solutions,
            resolution=resolution,
            help_needed=help_needed,
            task_id=task_id,
            tags=tags,
        )
        return await self.create_entry(
            JournalEntryCreate(
                journal_id=entry.journal_id,
                type=entry.type,
                title=entry.title,
                content=entry.content,
                task_id=entry.task_id,
                tags=entry.tags,
                sentiment=entry.sentiment,
            )
        )

    async def add_general_entry(
        self,
        agent_id: UUID,
        title: str,
        content: str,
        task_id: UUID | None = None,
        session_id: UUID | None = None,
        tags: list[str] | None = None,
        is_private: bool = False,
    ) -> JournalEntry:
        """Add a general journal entry."""
        journal = await self.get_or_create_journal(agent_id)
        entry = create_general_entry(
            journal_id=journal.id,
            title=title,
            content=content,
            task_id=task_id,
            session_id=session_id,
            tags=tags,
            is_private=is_private,
        )
        return await self.create_entry(
            JournalEntryCreate(
                journal_id=entry.journal_id,
                type=entry.type,
                title=entry.title,
                content=entry.content,
                task_id=entry.task_id,
                session_id=entry.session_id,
                tags=entry.tags,
                is_private=entry.is_private,
            )
        )

    # =========================================================================
    # ANALYTICS
    # =========================================================================

    async def get_journal_stats(self, journal_id: UUID) -> JournalStats | None:
        """Get statistics for a journal."""
        result = await self._db.execute(
            select(JournalTable).where(JournalTable.id == journal_id)
        )
        journal_row = result.scalar_one_or_none()

        if not journal_row:
            return None

        return JournalStats(
            total_entries=journal_row.total_entries,
            entries_by_type=journal_row.entries_by_type,
            last_entry_at=journal_row.last_entry_at,
            has_summary=journal_row.latest_summary is not None,
        )

    async def get_growth_metrics(self, agent_id: UUID) -> GrowthMetrics | None:
        """
        Calculate growth metrics for an agent.

        Analyzes journal entries to track learning progress,
        struggle resolution, and sentiment trends.
        """
        journal = await self.get_journal_by_agent(agent_id)
        if not journal:
            return None

        entries_by_type = journal.entries_by_type

        # Count entries by type
        reflections = entries_by_type.get("task_reflection", 0)
        learnings = entries_by_type.get("learning", 0)
        struggles = entries_by_type.get("struggle", 0)
        decisions = entries_by_type.get("decision_log", 0)

        # Calculate struggle resolution rate
        # (struggles with "Resolution" section / total struggles)
        resolution_rate = 0.0
        if struggles > 0:
            result = await self._db.execute(
                select(func.count(JournalEntryTable.id)).where(
                    and_(
                        JournalEntryTable.journal_id == journal.id,
                        JournalEntryTable.type == JournalEntryType.STRUGGLE,
                        JournalEntryTable.content.contains("## Resolution"),
                    )
                )
            )
            resolved_count = result.scalar() or 0
            resolution_rate = resolved_count / struggles

        # Calculate learning frequency (learnings per day since first entry)
        learning_frequency = 0.0
        if journal.created_at and learnings > 0:
            days_active = max(1, (datetime.utcnow() - journal.created_at).days)
            learning_frequency = learnings / days_active

        # Simple sentiment trend based on recent entries
        # (in production, would use NLP sentiment analysis)
        sentiment_trend = "stable"

        return GrowthMetrics(
            total_reflections=reflections,
            total_learnings=learnings,
            total_struggles=struggles,
            total_decisions=decisions,
            struggle_resolution_rate=resolution_rate,
            learning_frequency=learning_frequency,
            sentiment_trend=sentiment_trend,
        )

    async def search_entries(
        self,
        agent_id: UUID,
        query: str,
        top_k: int = 5,
    ) -> list[JournalEntry]:
        """
        Semantic search through an agent's journal entries.

        Uses the Optimal API for RAG-based search.

        Args:
            agent_id: Agent whose journal to search
            query: Natural language query
            top_k: Number of results to return

        Returns:
            List of relevant journal entries
        """
        try:
            optimal = await self._get_optimal_service()
            from roboco.services.optimal import IndexType, QueryContext

            results = await optimal.search(
                query=query,
                context=QueryContext(
                    agent_id=agent_id,
                    index_types=[IndexType.JOURNALS],
                ),
                top_k=top_k,
            )

            # Extract entry IDs from results and fetch full entries
            entries = []
            for result in results:
                # Parse entry ID from the indexed content
                # (This is a simplified approach - in production we'd store
                # entry ID in metadata)
                if "Entry ID:" in result.content:
                    try:
                        entry_id_str = (
                            result.content.split("Entry ID:")[1].split("\n")[0].strip()
                        )
                        entry_id = UUID(entry_id_str)
                        entry = await self.get_entry(entry_id)
                        if entry:
                            entries.append(entry)
                    except (ValueError, IndexError):
                        pass

            return entries[:top_k]

        except Exception as e:
            logger.warning("Journal search failed", error=str(e))
            return []


def get_journal_service(db: AsyncSession) -> JournalService:
    """Factory function for JournalService."""
    return JournalService(db)
