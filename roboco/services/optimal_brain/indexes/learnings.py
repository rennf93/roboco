"""
Learnings Index Plugin

Handles indexing and searching cross-agent learnings.
Learnings are shareable knowledge that benefits all agents.
"""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from roboco.models.optimal import IndexType, SearchOutcome, SearchResult
from roboco.services.optimal_brain.indexes.base import BaseIndexPlugin, IngestResult


@dataclass
class RecordLearningParams:
    """Parameters for recording a learning."""

    content: str
    category: str
    agent_id: UUID | None = None
    agent_role: str | None = None
    task_id: UUID | None = None
    team: str | None = None
    shareable: bool = True
    tags: list[str] = field(default_factory=list)


class LearningsIndexPlugin(BaseIndexPlugin):
    """
    Plugin for indexing and searching cross-agent learnings.

    Enables organizational knowledge to compound:
    - Shareable learnings from all agents
    - Team-specific or global scope
    - Category-based filtering
    """

    @property
    def index_type(self) -> IndexType:
        return IndexType.LEARNINGS

    async def search_with_embedding(
        self,
        query_embedding: list[float],
        query_text: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
        *,
        include_private: bool = False,
    ) -> SearchOutcome:
        """Enforce ``shareable=True`` on cross-agent retrieval by default.

        A private LEARNING journal entry is recorded here with
        ``shareable=False`` (recorded for completeness, never meant to surface
        to other agents). The shared retrieval path — ``OptimalService.search``
        used by the briefing / ``similar_memory`` — calls here with no
        ``include_private``; the base ``_citations_to_results`` only filters
        when a ``shareable`` filter is present, so a ``shareable=False`` chunk
        would sail through into another agent's briefing (a private reflection
        leaked across the cross-agent corpus). Force ``shareable=True`` in the
        filters unless the caller explicitly opts into private view
        (``include_private=True`` — the ``search_learnings(shareable_only=False)``
        audit/admin path), in which case the caller's filters are respected
        as-is (no shareable filter → all entries).
        """
        effective = dict(filters) if filters else {}
        if not include_private:
            effective["shareable"] = True
        return await super().search_with_embedding(
            query_embedding, query_text, top_k=top_k, filters=effective
        )

    async def search(
        self,
        query: str,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
        *,
        include_private: bool = False,
    ) -> SearchOutcome:
        """Embed-then-search entry point; threads ``include_private`` to
        ``search_with_embedding`` so the shareable default applies to both
        entry points (``OptimalService.search`` calls ``search_with_embedding``
        directly; ``search_learnings`` / ``get_learnings_by_*`` call here)."""
        query_embedding = await self._compute_query_embedding(query)
        return await self.search_with_embedding(
            query_embedding,
            query,
            top_k=top_k,
            filters=filters,
            include_private=include_private,
        )

    def prepare_metadata(
        self,
        content: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Prepare metadata for learning."""
        del content  # Unused - metadata comes from kwargs
        return {
            "type": "learning",
            "category": kwargs.get("category", "general"),
            "agent_id": str(kwargs.get("agent_id", "")),
            "agent_role": kwargs.get("agent_role", ""),
            "task_id": str(kwargs.get("task_id")) if kwargs.get("task_id") else "none",
            "team": kwargs.get("team", ""),
            "shareable": kwargs.get("shareable", True),
            "tags": kwargs.get("tags", []),
        }

    def build_source_uri(
        self,
        doc_id: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Build source URI for learning."""
        del kwargs  # Unused - URI uses doc_id only
        learning_id = doc_id or "lrn-unknown"
        return f"roboco://learnings/{learning_id}"

    async def record_learning(self, params: RecordLearningParams) -> IngestResult:
        """
        Record a learning that can help future agents.

        Args:
            params: RecordLearningParams containing:
                - content: The learning content
                - category: Category (error_handling, performance, testing, etc.)
                - agent_id: ID of the agent who learned this
                - agent_role: Role of the agent
                - task_id: Related task ID
                - team: Team (backend, frontend, ux_ui)
                - shareable: Whether other agents can find this
                - tags: Additional tags

        Returns:
            IngestResult with ingestion details
        """
        import hashlib

        # Generate learning ID
        content_hash = hashlib.md5(
            params.content[:100].encode(), usedforsecurity=False
        ).hexdigest()[:12]
        learning_id = f"lrn-{content_hash}"

        # Build enriched content
        parts = [f"Category: {params.category}"]
        if params.team:
            parts.append(f"Team: {params.team}")
        if params.agent_role:
            parts.append(f"From: {params.agent_role}")
        parts.append(f"\nLearning:\n{params.content}")
        if params.tags:
            parts.append(f"\nTags: {', '.join(params.tags)}")

        enriched_content = "\n".join(parts)

        return await self.ingest(
            content=enriched_content,
            doc_id=learning_id,
            category=params.category,
            agent_id=params.agent_id,
            agent_role=params.agent_role,
            task_id=params.task_id,
            team=params.team,
            shareable=params.shareable,
            tags=params.tags,
        )

    async def search_learnings(
        self,
        query: str,
        category: str | None = None,
        team: str | None = None,
        shareable_only: bool = True,
        top_k: int = 10,
    ) -> list[SearchResult]:
        """
        Search for relevant learnings.

        Args:
            query: Search query
            category: Optional category filter
            team: Optional team filter
            shareable_only: Only return shareable learnings
            top_k: Number of results

        Returns:
            List of matching learnings
        """
        filters: dict[str, Any] = {}
        if category:
            filters["category"] = category
        if team:
            filters["team"] = team
        if shareable_only:
            filters["shareable"] = True

        # ``shareable_only=False`` is the explicit opt-in to the private/admin
        # view of the corpus — thread ``include_private=True`` so the plugin's
        # shareable default (forced on every other shared path) is NOT applied.
        outcome = await self.search(
            query=query,
            top_k=top_k,
            filters=filters,
            include_private=not shareable_only,
        )
        return outcome.results

    async def get_learnings_by_category(
        self,
        category: str,
        top_k: int = 20,
    ) -> list[SearchResult]:
        """Get all learnings in a category."""
        outcome = await self.search(
            query=f"learnings about {category}",
            top_k=top_k,
            filters={"category": category},
        )
        return outcome.results

    async def get_learnings_by_role(
        self,
        agent_role: str,
        top_k: int = 20,
    ) -> list[SearchResult]:
        """Get learnings from agents with a specific role."""
        outcome = await self.search(
            query=f"learnings from {agent_role}",
            top_k=top_k,
            filters={"agent_role": agent_role},
        )
        return outcome.results

    async def get_team_learnings(
        self,
        team: str,
        top_k: int = 20,
    ) -> list[SearchResult]:
        """Get learnings from a specific team."""
        outcome = await self.search(
            query="team learnings",
            top_k=top_k,
            filters={"team": team},
        )
        return outcome.results
