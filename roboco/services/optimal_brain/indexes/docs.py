"""
Documentation Index Plugin

Handles indexing and searching documentation files (markdown, text, etc.).
"""

from typing import Any

from roboco.models.optimal import IndexType
from roboco.services.optimal_brain.indexes.base import BaseIndexPlugin


class DocsIndexPlugin(BaseIndexPlugin):
    """
    Plugin for indexing and searching documentation.

    Handles:
    - Markdown files
    - Text files
    - API documentation
    - README files
    """

    @property
    def index_type(self) -> IndexType:
        return IndexType.DOCUMENTATION

    def prepare_metadata(
        self,
        content: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Prepare metadata for documentation content."""
        del content  # Unused - metadata comes from kwargs
        return {
            "type": "documentation",
            "project": kwargs.get("project", "default"),
            "doc_type": kwargs.get("doc_type", "general"),  # readme, api, guide, etc.
            "file_path": kwargs.get("file_path"),
        }

    def build_source_uri(self, doc_id: str | None = None, **kwargs: Any) -> str:
        """Build source URI for documentation."""
        file_path = kwargs.get("file_path", doc_id or "unknown")
        return f"roboco://docs/{file_path}"

    async def index_sources(
        self,
        sources: list[str],
        _project: str | None = None,
    ) -> int:
        """
        Index documentation files/directories.

        Args:
            sources: List of file paths, directories, URLs, or glob patterns
            project: Optional project identifier for filtering

        Returns:
            Number of documents indexed
        """
        return await self.add_sources(sources)
