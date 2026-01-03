"""
Journals Index Plugin

Handles indexing and searching agent journal entries.
"""

from typing import Any
from uuid import UUID

from roboco.models.optimal import IndexJournalEntryParams, IndexType
from roboco.services.optimal_brain.indexes.base import BaseIndexPlugin


class JournalsIndexPlugin(BaseIndexPlugin):
    """
    Plugin for indexing and searching agent journal entries.

    Handles:
    - Task reflections
    - Decision logs
    - Learning entries
    - Struggle entries
    - General notes
    """

    @property
    def index_type(self) -> IndexType:
        return IndexType.JOURNALS

    def prepare_metadata(
        self,
        content: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Prepare metadata for journal entry."""
        del content  # Unused - metadata comes from kwargs
        return {
            "type": "journal",
            "entry_id": str(kwargs.get("entry_id", "")),
            "agent_id": str(kwargs.get("agent_id", "")),
            "entry_type": kwargs.get("entry_type", "general"),
            "task_id": str(kwargs.get("task_id")) if kwargs.get("task_id") else "none",
            "tags": kwargs.get("tags", []),
        }

    def build_source_uri(self, doc_id: str | None = None, **kwargs: Any) -> str:
        """Build source URI for journal entry."""
        entry_id = kwargs.get("entry_id", doc_id or "unknown")
        return f"roboco://journals/{entry_id}"

    async def index_entry(self, params: IndexJournalEntryParams) -> None:
        """
        Index a journal entry.

        Args:
            params: IndexJournalEntryParams containing entry details
        """
        await self.ingest(
            content=params.content,
            doc_id=str(params.entry_id)[:50],
            entry_id=params.entry_id,
            agent_id=params.agent_id,
            entry_type=params.entry_type,
            task_id=params.task_id,
            tags=params.tags or [],
        )

    async def search_by_agent(
        self,
        query: str,
        agent_id: UUID,
        top_k: int = 5,
    ) -> list:
        """Search journal entries for a specific agent."""
        outcome = await self.search(
            query=query,
            top_k=top_k,
            filters={"agent_id": str(agent_id)},
        )
        return outcome.results

    async def search_by_type(
        self,
        query: str,
        entry_type: str,
        top_k: int = 5,
    ) -> list:
        """Search journal entries of a specific type."""
        outcome = await self.search(
            query=query,
            top_k=top_k,
            filters={"entry_type": entry_type},
        )
        return outcome.results
