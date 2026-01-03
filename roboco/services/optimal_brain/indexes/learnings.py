"""
Learnings Index Plugin

Handles indexing and searching cross-agent learnings.
Learnings are shareable knowledge that benefits all agents.
"""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from roboco.models.optimal import IndexType, SearchResult
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
        content_hash = hashlib.md5(params.content[:100].encode()).hexdigest()[:12]
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

        outcome = await self.search(query=query, top_k=top_k, filters=filters)
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
