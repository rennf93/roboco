"""
Errors Index Plugin

Handles indexing and searching error patterns and solutions.
Global scope - all agents learn from all errors.
"""

from typing import Any

from roboco.models.optimal import IndexErrorParams, IndexType, SearchResult
from roboco.services.optimal_brain.indexes.base import BaseIndexPlugin, IngestResult


class ErrorsIndexPlugin(BaseIndexPlugin):
    """
    Plugin for indexing and searching error patterns.

    Enables self-healing through collective error memory:
    - Records error patterns with solutions
    - Tracks which solutions worked
    - Global scope - all teams benefit
    """

    @property
    def index_type(self) -> IndexType:
        return IndexType.ERRORS

    def prepare_metadata(
        self,
        content: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Prepare metadata for error pattern."""
        del content  # Unused - metadata comes from kwargs
        return {
            "type": "error",
            "error_message": kwargs.get("error_message", ""),
            "worked": kwargs.get("worked", True),
            "agent_id": str(kwargs.get("agent_id", "")),
            "task_id": str(kwargs.get("task_id")) if kwargs.get("task_id") else "none",
            "team": kwargs.get("team", "unknown"),
            "tags": kwargs.get("tags", []),
        }

    def build_source_uri(
        self,
        doc_id: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Build source URI for error pattern."""
        del kwargs  # Unused - URI uses doc_id only
        error_id = doc_id or "err-unknown"
        return f"roboco://errors/{error_id}"

    def _build_content(self, params: IndexErrorParams) -> str:
        """Build searchable content from error params."""
        parts = [
            f"Error: {params.error_message}",
            f"Context: {params.context}",
            f"Solution: {params.solution}",
        ]
        if params.tags:
            parts.append(f"Tags: {', '.join(params.tags)}")
        return "\n\n".join(parts)

    async def record_error(self, params: IndexErrorParams) -> IngestResult:
        """
        Record an error pattern with its solution.

        Args:
            params: IndexErrorParams containing error details

        Returns:
            IngestResult with ingestion details
        """
        import hashlib

        # Generate a unique ID from error message
        error_hash = hashlib.md5(
            params.error_message.encode(), usedforsecurity=False
        ).hexdigest()[:12]
        error_id = f"err-{error_hash}"

        content = self._build_content(params)

        return await self.ingest(
            content=content,
            doc_id=error_id,
            error_message=params.error_message,
            worked=params.worked,
            agent_id=params.agent_id,
            task_id=params.task_id,
            team=params.team,
            tags=params.tags or [],
        )

    async def search_error(
        self,
        error_message: str,
        context: str = "",
        top_k: int = 5,
    ) -> list[SearchResult]:
        """
        Search for known solutions to an error.

        Args:
            error_message: The error message to search for
            context: Additional context about the error
            top_k: Number of results to return

        Returns:
            List of matching error patterns with solutions
        """
        query = f"Error: {error_message}"
        if context:
            query += f" Context: {context}"

        outcome = await self.search(query=query, top_k=top_k)
        results = outcome.results

        # Boost results where worked=True
        for result in results:
            if result.metadata.get("worked", False):
                result.score *= 1.2  # 20% boost for working solutions

        return sorted(results, key=lambda r: r.score, reverse=True)

    async def search_by_team(
        self,
        query: str,
        team: str,
        top_k: int = 5,
    ) -> list[SearchResult]:
        """Search errors from a specific team."""
        outcome = await self.search(
            query=query,
            top_k=top_k,
            filters={"team": team},
        )
        return outcome.results
