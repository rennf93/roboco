"""
Journal API Service

Manages agent personal journals for reflection, growth tracking, and debugging.
Each agent has their own journal with entries tied to tasks and sessions.
Integrates with the Optimal API for RAG indexing of entries.
"""

from datetime import UTC, datetime
from typing import Any, ClassVar
from uuid import UUID

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.db.tables import JournalEntryTable, JournalTable
from roboco.foundation.policy.journaling import (
    SCOPE_TO_TYPE as _FOUNDATION_SCOPE_TO_TYPE,
)
from roboco.models.base import JournalEntryType
from roboco.models.journal import (
    DecisionLogParams,
    GeneralEntryParams,
    GrowthMetrics,
    Journal,
    JournalEntry,
    JournalEntryCreate,
    JournalStats,
    LearningEntryParams,
    ListEntriesFilter,
    StruggleEntryParams,
    TaskReflectionParams,
    create_decision_log,
    create_general_entry,
    create_learning_entry,
    create_struggle_entry,
    create_task_reflection,
)
from roboco.models.optimal import IndexJournalEntryParams
from roboco.services.base import BaseService
from roboco.utils.converters import require_uuid, to_python_uuid

# Scope mapping is canonical in foundation.policy.journaling.
# Derived as string-keyed dict here because the service's call sites pass
# scope strings (from the gateway's content_actions layer) not Scope enums.
_SCOPE_TO_TYPE: dict[str, JournalEntryType] = {
    scope.value: entry_type for scope, entry_type in _FOUNDATION_SCOPE_TO_TYPE.items()
}


class JournalService(BaseService):
    """
    Service for managing agent journals.

    Provides CRUD operations for journals and entries,
    plus analytics and growth tracking.
    """

    service_name: ClassVar[str] = "journal"

    def __init__(self, db: AsyncSession) -> None:
        super().__init__(db)
        self._optimal_service: Any = None  # Lazy loaded to avoid circular import

    async def _get_optimal_service(self) -> Any:
        """Lazy load the OptimalService."""
        if self._optimal_service is None:
            from roboco.services.optimal import get_optimal_service

            self._optimal_service = await get_optimal_service()
        return self._optimal_service

    async def resolve_agent_id(self, agent_id_or_slug: str) -> UUID | None:
        """
        Resolve an agent identifier to a UUID.

        Accepts either a UUID string or an agent slug (e.g., "be-dev-1").

        Args:
            agent_id_or_slug: UUID string or agent slug

        Returns:
            The agent's UUID, or None if not found
        """
        from roboco.services.repositories import resolve_agent_uuid

        return await resolve_agent_uuid(self.session, agent_id_or_slug)

    async def get_agent_slug(self, agent_id: UUID) -> str | None:
        """
        Get an agent's slug from their UUID.

        Args:
            agent_id: The agent's UUID

        Returns:
            The agent's slug (e.g., "be-dev-1"), or None if not found
        """
        from roboco.services.repositories import get_agent_slug

        return await get_agent_slug(self.session, agent_id)

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
        result = await self.session.execute(
            select(JournalTable).where(JournalTable.agent_id == agent_id)
        )
        journal_row = result.scalar_one_or_none()

        if journal_row:
            return Journal(
                id=require_uuid(journal_row.id),
                agent_id=require_uuid(journal_row.agent_id),
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
        self.session.add(new_journal)
        await self.session.commit()
        await self.session.refresh(new_journal)

        self.log.info(
            "Created new journal",
            agent_id=str(agent_id),
            journal_id=str(new_journal.id),
        )

        return Journal(
            id=require_uuid(new_journal.id),
            agent_id=require_uuid(new_journal.agent_id),
            total_entries=0,
            created_at=new_journal.created_at,
        )

    async def get_journal(self, journal_id: UUID) -> Journal | None:
        """Get a journal by ID."""
        result = await self.session.execute(
            select(JournalTable).where(JournalTable.id == journal_id)
        )
        journal_row = result.scalar_one_or_none()

        if not journal_row:
            return None

        return Journal(
            id=require_uuid(journal_row.id),
            agent_id=require_uuid(journal_row.agent_id),
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
        result = await self.session.execute(
            select(JournalTable).where(JournalTable.agent_id == agent_id)
        )
        journal_row = result.scalar_one_or_none()

        if not journal_row:
            return None

        return Journal(
            id=require_uuid(journal_row.id),
            agent_id=require_uuid(journal_row.agent_id),
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

    async def create_entry(
        self, entry_create: JournalEntryCreate
    ) -> JournalEntry | None:
        """
        Create a new journal entry.

        Args:
            entry_create: Entry creation schema

        Returns:
            The created entry, or None if the referenced task/session/journal
            no longer exists (e.g. after a runtime reset).
        """
        from sqlalchemy.exc import IntegrityError

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
        self.session.add(entry_row)

        # Update journal metadata
        result = await self.session.execute(
            select(JournalTable).where(JournalTable.id == entry_create.journal_id)
        )
        journal_row = result.scalar_one_or_none()

        if journal_row:
            journal_row.total_entries += 1
            journal_row.last_entry_at = datetime.now(UTC)
            type_key = (
                entry_create.type.value
                if isinstance(entry_create.type, JournalEntryType)
                else entry_create.type
            )
            entries_by_type = dict(journal_row.entries_by_type)
            entries_by_type[type_key] = entries_by_type.get(type_key, 0) + 1
            journal_row.entries_by_type = entries_by_type

        try:
            await self.session.commit()
        except IntegrityError as e:
            await self.session.rollback()
            self.log.warning(
                "Journal entry skipped - referenced row was deleted",
                journal_id=str(entry_create.journal_id),
                task_id=str(entry_create.task_id) if entry_create.task_id else None,
                session_id=str(entry_create.session_id)
                if entry_create.session_id
                else None,
                error=str(e.orig),
            )
            return None

        await self.session.refresh(entry_row)

        self.log.info(
            "Created journal entry",
            entry_id=str(entry_row.id),
            journal_id=str(entry_create.journal_id),
            type=entry_create.type,
        )

        # Index in RAG - non-blocking
        try:
            optimal = await self._get_optimal_service()
            # Convert SQLAlchemy UUIDs to Python UUIDs for the params
            agent_id_for_index = (
                require_uuid(journal_row.agent_id)
                if journal_row
                else entry_create.journal_id
            )
            await optimal.index_journal_entry(
                IndexJournalEntryParams(
                    entry_id=require_uuid(entry_row.id),
                    agent_id=agent_id_for_index,
                    content=entry_create.content,
                    entry_type=type_key,
                    task_id=entry_create.task_id,
                    tags=entry_create.tags,
                )
            )

            # Also index LEARNING entries to the learnings index for cross-agent sharing
            if type_key == JournalEntryType.LEARNING.value:
                from roboco.services.optimal_brain.indexes.learnings import (
                    RecordLearningParams,
                )

                await optimal.record_learning(
                    RecordLearningParams(
                        content=entry_create.content,
                        category="journal_learning",
                        agent_id=agent_id_for_index,
                        task_id=entry_create.task_id,
                        shareable=not entry_create.is_private,
                        tags=entry_create.tags,
                    )
                )
        except Exception as e:
            self.log.warning("Failed to index journal entry in RAG", error=str(e))

        return JournalEntry(
            id=require_uuid(entry_row.id),
            journal_id=require_uuid(entry_row.journal_id),
            type=entry_row.type,
            title=entry_row.title,
            content=entry_row.content,
            task_id=to_python_uuid(entry_row.task_id),
            session_id=to_python_uuid(entry_row.session_id),
            timestamp=entry_row.timestamp,
            tags=entry_row.tags,
            sentiment=entry_row.sentiment,
            is_private=entry_row.is_private,
            created_at=entry_row.created_at,
        )

    async def get_entry(self, entry_id: UUID) -> JournalEntry | None:
        """Get a journal entry by ID."""
        result = await self.session.execute(
            select(JournalEntryTable).where(JournalEntryTable.id == entry_id)
        )
        entry_row = result.scalar_one_or_none()

        if not entry_row:
            return None

        return JournalEntry(
            id=require_uuid(entry_row.id),
            journal_id=require_uuid(entry_row.journal_id),
            type=entry_row.type,
            title=entry_row.title,
            content=entry_row.content,
            task_id=to_python_uuid(entry_row.task_id),
            session_id=to_python_uuid(entry_row.session_id),
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
        filters: ListEntriesFilter | None = None,
    ) -> list[JournalEntry]:
        """
        List journal entries with filtering.

        Args:
            journal_id: Journal to list entries from
            filters: Optional filter parameters

        Returns:
            List of journal entries
        """
        f = filters or ListEntriesFilter()
        query = select(JournalEntryTable).where(
            JournalEntryTable.journal_id == journal_id
        )

        if f.entry_type:
            query = query.where(JournalEntryTable.type == f.entry_type)

        if f.task_id:
            query = query.where(JournalEntryTable.task_id == f.task_id)

        if not f.include_private:
            query = query.where(JournalEntryTable.is_private.is_(False))

        query = query.order_by(JournalEntryTable.timestamp.desc())
        query = query.limit(f.limit).offset(f.offset)

        result = await self.session.execute(query)
        rows = result.scalars().all()

        return [
            JournalEntry(
                id=require_uuid(row.id),
                journal_id=require_uuid(row.journal_id),
                type=row.type,
                title=row.title,
                content=row.content,
                task_id=to_python_uuid(row.task_id),
                session_id=to_python_uuid(row.session_id),
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
        result = await self.session.execute(
            select(JournalEntryTable).where(JournalEntryTable.id == entry_id)
        )
        entry_row = result.scalar_one_or_none()

        if not entry_row:
            return False

        # Update journal metadata
        journal_result = await self.session.execute(
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

        await self.session.delete(entry_row)
        await self.session.commit()

        self.log.info("Deleted journal entry", entry_id=str(entry_id))
        return True

    # =========================================================================
    # CONVENIENCE METHODS
    # =========================================================================

    async def add_task_reflection(
        self,
        agent_id: UUID,
        params: TaskReflectionParams,
    ) -> JournalEntry | None:
        """Add a task reflection entry."""
        journal = await self.get_or_create_journal(agent_id)
        # Update journal_id in params
        params_with_journal = TaskReflectionParams(
            journal_id=journal.id,
            task_id=params.task_id,
            title=params.title,
            what_done=params.what_done,
            what_learned=params.what_learned,
            what_struggled=params.what_struggled,
            next_steps=params.next_steps,
            tags=params.tags,
        )
        entry = create_task_reflection(params_with_journal)
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
        params: DecisionLogParams,
    ) -> JournalEntry | None:
        """Add a decision log entry."""
        journal = await self.get_or_create_journal(agent_id)
        params_with_journal = DecisionLogParams(
            journal_id=journal.id,
            title=params.title,
            context=params.context,
            options=params.options,
            chosen=params.chosen,
            rationale=params.rationale,
            consequences=params.consequences,
            task_id=params.task_id,
            tags=params.tags,
        )
        entry = create_decision_log(params_with_journal)
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
        params: LearningEntryParams,
    ) -> JournalEntry | None:
        """Add a learning entry."""
        journal = await self.get_or_create_journal(agent_id)
        params_with_journal = LearningEntryParams(
            journal_id=journal.id,
            title=params.title,
            what_learned=params.what_learned,
            how_applied=params.how_applied,
            source=params.source,
            task_id=params.task_id,
            tags=params.tags,
        )
        entry = create_learning_entry(params_with_journal)
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
        params: StruggleEntryParams,
    ) -> JournalEntry | None:
        """Add a struggle entry."""
        journal = await self.get_or_create_journal(agent_id)
        params_with_journal = StruggleEntryParams(
            journal_id=journal.id,
            title=params.title,
            what_struggled=params.what_struggled,
            attempted_solutions=params.attempted_solutions,
            resolution=params.resolution,
            help_needed=params.help_needed,
            task_id=params.task_id,
            tags=params.tags,
        )
        entry = create_struggle_entry(params_with_journal)
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
        params: GeneralEntryParams,
    ) -> JournalEntry | None:
        """Add a general journal entry."""
        journal = await self.get_or_create_journal(agent_id)
        params_with_journal = GeneralEntryParams(
            journal_id=journal.id,
            title=params.title,
            content=params.content,
            task_id=params.task_id,
            session_id=params.session_id,
            tags=params.tags,
            is_private=params.is_private,
        )
        entry = create_general_entry(params_with_journal)
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
        result = await self.session.execute(
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
            result = await self.session.execute(
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
            days_active = max(1, (datetime.now(UTC) - journal.created_at).days)
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
                # Get entry_id from metadata (stored during indexing)
                entry_id_str = result.metadata.get("entry_id")
                if entry_id_str:
                    try:
                        entry_id = UUID(entry_id_str)
                        entry = await self.get_entry(entry_id)
                        if entry:
                            # Filter by agent_id to ensure we only
                            # return this agent's entries
                            journal = await self.get_journal(agent_id)
                            if journal and entry.journal_id == journal.id:
                                entries.append(entry)
                    except ValueError:
                        self.log.warning(
                            "Invalid entry_id in search result",
                            entry_id=entry_id_str,
                        )

            self.log.debug(
                "Journal search completed",
                agent_id=str(agent_id),
                query=query[:50],
                results_found=len(entries),
            )
            return entries[:top_k]

        except Exception as e:
            self.log.error(
                "Journal search failed",
                agent_id=str(agent_id),
                query=query[:50] if query else "empty",
                error=str(e),
                error_type=type(e).__name__,
            )
            return []

    # =========================================================================
    # GATEWAY (CHOREOGRAPHER) BACKFILL
    #
    # Existence checks the gateway uses to enforce tracing requirements
    # (every transition demands a journal entry of the appropriate type).
    # =========================================================================

    async def _has_entry_of_type(
        self,
        agent_id: UUID,
        task_id: UUID,
        entry_type: JournalEntryType,
    ) -> bool:
        """True iff agent has a journal entry of `entry_type` for this task."""
        query = (
            select(func.count(JournalEntryTable.id))
            .join(JournalTable, JournalEntryTable.journal_id == JournalTable.id)
            .where(
                JournalTable.agent_id == agent_id,
                JournalEntryTable.task_id == task_id,
                JournalEntryTable.type == entry_type,
            )
        )
        result = await self.session.execute(query)
        return (result.scalar() or 0) > 0

    async def has_decision_for_task(self, agent_id: UUID, task_id: UUID) -> bool:
        """True iff a DECISION_LOG entry exists for (agent, task)."""
        return await self._has_entry_of_type(
            agent_id, task_id, JournalEntryType.DECISION_LOG
        )

    async def latest_decision_at(
        self, agent_id: UUID, task_id: UUID
    ) -> datetime | None:
        """Return ``created_at`` of the most recent DECISION_LOG entry for
        (agent, task), or ``None`` if no decision exists.

        Backs the windowed-satisfaction variant of the PM-decision
        tracing gate: the choreographer treats decisions older than
        ``settings.pm_decision_window_seconds`` as missing so PMs write a
        fresh decision around each decision point.
        """
        query = (
            select(func.max(JournalEntryTable.created_at))
            .join(JournalTable, JournalEntryTable.journal_id == JournalTable.id)
            .where(
                JournalTable.agent_id == agent_id,
                JournalEntryTable.task_id == task_id,
                JournalEntryTable.type == JournalEntryType.DECISION_LOG,
            )
        )
        result = await self.session.execute(query)
        return result.scalar()

    async def has_note_for_task(self, agent_id: UUID, task_id: UUID) -> bool:
        """True iff a GENERAL (scope='note') entry exists for (agent, task).

        Backs the JOURNAL_NOTE_AT_CLAIM tracing requirement on
        i_will_work_on (pre-gateway parity P1: developers wrote a
        work_log/note entry on every claim).
        """
        return await self._has_entry_of_type(
            agent_id, task_id, JournalEntryType.GENERAL
        )

    async def has_learning_for_task(self, agent_id: UUID, task_id: UUID) -> bool:
        """True iff a LEARNING entry exists for (agent, task)."""
        return await self._has_entry_of_type(
            agent_id, task_id, JournalEntryType.LEARNING
        )

    async def has_reflect_for_task(self, agent_id: UUID, task_id: UUID) -> bool:
        """True iff a TASK_REFLECTION entry exists for (agent, task)."""
        return await self._has_entry_of_type(
            agent_id, task_id, JournalEntryType.TASK_REFLECTION
        )

    async def has_struggle_for_task(self, agent_id: UUID, task_id: UUID) -> bool:
        """True iff a STRUGGLE entry exists for (agent, task)."""
        return await self._has_entry_of_type(
            agent_id, task_id, JournalEntryType.STRUGGLE
        )

    async def write_struggle(
        self,
        *,
        agent_id: UUID,
        task_id: UUID,
        content: str,
    ) -> JournalEntry | None:
        """Write a STRUGGLE entry for (agent, task) with `content` as body.

        Title is derived from the first line of content (truncated to 100
        chars) so the entry has a meaningful summary in lists.
        """
        first_line = content.strip().splitlines()[0] if content.strip() else "Struggle"
        title = first_line[:100]
        params = StruggleEntryParams(
            title=title,
            what_struggled=content,
            attempted_solutions=[],
            task_id=task_id,
        )
        return await self.add_struggle(agent_id, params)

    async def write_entry(
        self,
        *,
        agent_id: UUID,
        title: str,
        content: str,
        scope: str = "note",
        task_id: UUID | None = None,
    ) -> JournalEntry | None:
        """Gateway adapter — write a journal entry by `scope` string.

        The gateway speaks in scope strings (`note`, `decision`, `reflect`,
        `learning`, `struggle`) while the service stores entries by
        `JournalEntryType` enum keyed off a `journal_id` (which the agent
        doesn't carry). This adapter resolves both: maps scope to the enum,
        looks up or creates the agent's journal, then delegates to
        `create_entry(JournalEntryCreate(...))`.

        Raises:
            ValueError: If `scope` is not one of the supported gateway
                scopes. Caller (gateway) validates the scope set before
                reaching here, but the guard is kept defensive.
        """
        entry_type = _SCOPE_TO_TYPE.get(scope)
        if entry_type is None:
            raise ValueError(
                f"unknown scope {scope!r}; expected one of {sorted(_SCOPE_TO_TYPE)}"
            )
        journal = await self.get_or_create_journal(agent_id)
        return await self.create_entry(
            JournalEntryCreate(
                journal_id=journal.id,
                type=entry_type,
                title=title,
                content=content,
                task_id=task_id,
            )
        )


def get_journal_service(db: AsyncSession) -> JournalService:
    """Factory function for JournalService."""
    return JournalService(db)
