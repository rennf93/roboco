"""
Code Index Plugin

Handles indexing and searching code files and repositories.
"""

from typing import Any

from roboco.models.optimal import IndexType
from roboco.services.optimal_brain.indexes.base import BaseIndexPlugin


class CodeIndexPlugin(BaseIndexPlugin):
    """
    Plugin for indexing and searching code.

    Handles:
    - Source code files (Python, TypeScript, etc.)
    - Repository directories
    - Glob patterns for selective indexing
    """

    @property
    def index_type(self) -> IndexType:
        return IndexType.CODE

    def prepare_metadata(
        self,
        content: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Prepare metadata for code content."""
        del content  # Unused - metadata comes from kwargs
        return {
            "type": "code",
            "project": kwargs.get("project", "default"),
            "language": kwargs.get("language"),
            "file_path": kwargs.get("file_path"),
        }

    def build_source_uri(self, doc_id: str | None = None, **kwargs: Any) -> str:
        """Build source URI for code."""
        file_path = kwargs.get("file_path", doc_id or "unknown")
        return f"roboco://code/{file_path}"

    async def index_sources(
        self,
        sources: list[str],
        _project: str | None = None,
    ) -> int:
        """
        Index code files/directories.

        Args:
            sources: List of file paths, directories, or glob patterns
            project: Optional project identifier for filtering

        Returns:
            Number of documents indexed
        """
        return await self.add_sources(sources)
