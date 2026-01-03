"""
Decisions Index Plugin

Handles indexing and searching architectural and design decisions.
Enables checking for precedents before making new decisions.
"""

from typing import Any
from uuid import UUID

from roboco.models.optimal import Decision, IndexDecisionParams, IndexType, SearchResult
from roboco.services.optimal_brain.indexes.base import BaseIndexPlugin, IngestResult


class DecisionsIndexPlugin(BaseIndexPlugin):
    """
    Plugin for indexing and searching decisions.

    Enables decision memory:
    - Check if a similar decision was made before
    - Understand rationale for past choices
    - Maintain architectural consistency
    """

    @property
    def index_type(self) -> IndexType:
        return IndexType.DECISIONS

    def prepare_metadata(
        self,
        content: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Prepare metadata for decision."""
        del content  # Unused - metadata comes from kwargs
        return {
            "type": "decision",
            "topic": kwargs.get("topic", ""),
            "decision": kwargs.get("decision", ""),
            "agent_id": str(kwargs.get("agent_id", "")),
            "task_id": str(kwargs.get("task_id")) if kwargs.get("task_id") else "none",
            "scope": kwargs.get("scope", "team"),
            "tags": kwargs.get("tags", []),
        }

    def build_source_uri(
        self,
        doc_id: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Build source URI for decision."""
        del kwargs  # Unused - URI uses doc_id only
        decision_id = doc_id or "dec-unknown"
        return f"roboco://decisions/{decision_id}"

    def _build_content(self, params: IndexDecisionParams) -> str:
        """Build searchable content from decision params."""
        parts = [
            f"Topic: {params.topic}",
            f"Decision: {params.decision}",
            f"Rationale: {params.rationale}",
        ]

        if params.context:
            parts.append(f"Context: {params.context}")

        if params.alternatives:
            alt_text = []
            for alt in params.alternatives:
                alt_text.append(f"- {alt.get('name', 'Unknown')}")
                if alt.get("pros"):
                    alt_text.append(f"  Pros: {', '.join(alt['pros'])}")
                if alt.get("cons"):
                    alt_text.append(f"  Cons: {', '.join(alt['cons'])}")
            parts.append("Alternatives considered:\n" + "\n".join(alt_text))

        if params.tags:
            parts.append(f"Tags: {', '.join(params.tags)}")

        return "\n\n".join(parts)

    async def record_decision(self, params: IndexDecisionParams) -> IngestResult:
        """
        Record an architectural or design decision.

        Args:
            params: IndexDecisionParams containing decision details

        Returns:
            IngestResult with ingestion details
        """
        import hashlib

        # Generate decision ID from topic
        topic_hash = hashlib.md5(params.topic.encode()).hexdigest()[:12]
        decision_id = f"dec-{topic_hash}"

        content = self._build_content(params)

        return await self.ingest(
            content=content,
            doc_id=decision_id,
            topic=params.topic,
            decision=params.decision,
            agent_id=params.agent_id,
            task_id=params.task_id,
            scope=params.scope,
            tags=params.tags or [],
        )

    async def check_decision(
        self,
        topic: str,
        threshold: float = 0.7,
        top_k: int = 5,
    ) -> list[Decision]:
        """
        Check if a similar decision was made before.

        Args:
            topic: Topic to check for precedents
            threshold: Minimum similarity score (0-1)
            top_k: Maximum number of precedents to return

        Returns:
            List of similar past decisions
        """
        outcome = await self.search(query=topic, top_k=top_k)

        decisions = []
        for result in outcome.results:
            if result.score >= threshold:
                decisions.append(
                    Decision(
                        decision_id=result.source.split("/")[-1],
                        topic=result.metadata.get("topic", "Unknown"),
                        decision=result.metadata.get("decision", ""),
                        rationale=self._extract_rationale(result.content),
                        context=self._extract_context(result.content),
                        agent_id=UUID(result.metadata["agent_id"])
                        if result.metadata.get("agent_id")
                        else None,
                        task_id=UUID(result.metadata["task_id"])
                        if result.metadata.get("task_id")
                        and result.metadata["task_id"] != "none"
                        else None,
                        scope=result.metadata.get("scope", "team"),
                        tags=result.metadata.get("tags", []),
                    )
                )

        return decisions

    def _extract_rationale(self, content: str) -> str:
        """Extract rationale section from content."""
        if "Rationale:" in content:
            parts = content.split("Rationale:")
            if len(parts) > 1:
                rationale = parts[1].split("\n\n")[0].strip()
                return rationale
        return ""

    def _extract_context(self, content: str) -> str:
        """Extract context section from content."""
        if "Context:" in content:
            parts = content.split("Context:")
            if len(parts) > 1:
                context = parts[1].split("\n\n")[0].strip()
                return context
        return ""

    async def search_by_scope(
        self,
        query: str,
        scope: str,
        top_k: int = 5,
    ) -> list[SearchResult]:
        """Search decisions by scope (team or org)."""
        outcome = await self.search(
            query=query,
            top_k=top_k,
            filters={"scope": scope},
        )
        return outcome.results

    async def search_by_agent(
        self,
        query: str,
        agent_id: UUID,
        top_k: int = 5,
    ) -> list[SearchResult]:
        """Search decisions made by a specific agent."""
        outcome = await self.search(
            query=query,
            top_k=top_k,
            filters={"agent_id": str(agent_id)},
        )
        return outcome.results
