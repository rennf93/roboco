"""
Documentation Index Plugin

Handles indexing and searching documentation files (markdown, text, etc.).
"""

from pathlib import Path
from typing import Any

import structlog

from roboco.models.optimal import IndexType
from roboco.services.optimal_brain.indexes.base import BaseIndexPlugin

logger = structlog.get_logger()

# Directories to skip during indexing
SKIP_DIRECTORIES = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".roboco",
    "node_modules",
    ".next",
    "dist",
    "build",
}


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

    def _expand_source(self, source: str) -> list[Path]:
        """Expand a source string into a list of candidate files.

        Accepts glob patterns, directory paths, or individual files. Returns
        an empty list for anything that doesn't resolve. Skips directories
        listed in SKIP_DIRECTORIES for recursive expansion.
        """
        source_path = Path(source)
        if "*" in source:
            return list(Path().glob(source))
        if source_path.is_dir():
            return self._expand_directory(source_path)
        return self._expand_file(source_path, source)

    def _expand_directory(self, source_path: Path) -> list[Path]:
        """Recursively collect indexable doc files under a directory."""
        return [
            f
            for pattern in ("*.md", "*.txt")
            for f in source_path.rglob(pattern)
            if not any(skip in f.parts for skip in SKIP_DIRECTORIES)
        ]

    def _expand_file(self, source_path: Path, source: str) -> list[Path]:
        """Resolve a single source path to an indexable doc file, or nothing."""
        if source_path.exists():
            # Only markdown/text files are docs; a recorded path to a source
            # file (e.g. a .tsx) is not indexable and is skipped quietly.
            if source_path.suffix.lower() in {".md", ".txt"}:
                return [source_path]
            logger.debug(f"Skipping non-doc source path: {source}")
            return []
        # A documenter may record a path for a doc that was not written under
        # the docs root; this is not actionable at index time, so log at debug
        # instead of flooding a warning on every pass.
        logger.debug(f"Doc source not found, skipping: {source}")
        return []

    def _read_file_record(
        self, file_path: Path, project: str | None
    ) -> dict[str, Any] | None:
        """Read one file into a dict the batch pipeline consumes.

        Returns None if the file shouldn't be indexed (wrong type, in a
        skip dir, unreadable) so callers can just filter None-s out.
        """
        if not file_path.is_file():
            return None
        if any(skip in file_path.parts for skip in SKIP_DIRECTORIES):
            return None
        try:
            content = file_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.warning(
                "Failed to read documentation file",
                file=str(file_path),
                error=str(e),
            )
            return None
        return {
            "content": content,
            "file_path": file_path,
            "doc_type": self._detect_doc_type(file_path),
            "project": project or "default",
        }

    def _collect_files(
        self, sources: list[str], project: str | None
    ) -> list[dict[str, Any]]:
        """Walk every source and return the readable file records."""
        files_data: list[dict[str, Any]] = []
        for source in sources:
            files = self._expand_source(source)
            logger.info(f"Found {len(files)} doc files to index in {source}")
            for file_path in files:
                record = self._read_file_record(file_path, project)
                if record is not None:
                    files_data.append(record)
        return files_data

    @staticmethod
    def _build_documents(
        files_data: list[dict[str, Any]],
    ) -> list[tuple[str, str | None, dict[str, Any]]]:
        """Shape the file records for `ingest_batch`."""
        return [
            (
                str(data["content"]),
                str(data["file_path"]),
                {
                    "file_path": str(data["file_path"]),
                    "doc_type": data["doc_type"],
                    "project": data["project"],
                },
            )
            for data in files_data
        ]

    @staticmethod
    def _build_indexed_files(
        files_data: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Shape the file records for the DB-tracking return payload."""
        return [
            {
                "source": str(data["file_path"].absolute()),
                "title": data["file_path"]
                .stem.replace("-", " ")
                .replace("_", " ")
                .title(),
                "preview": data["content"][:500] if data["content"] else None,
                "doc_type": data["doc_type"],
                "file_path": str(data["file_path"]),
            }
            for data in files_data
        ]

    async def index_sources(
        self,
        sources: list[str],
        project: str | None = None,
    ) -> tuple[int, list[dict[str, Any]]]:
        """
        Index documentation files/directories with batch embedding.

        Uses batch processing to embed all files together instead of one-by-one,
        achieving 10-15x speedup on large documentation sets.

        Args:
            sources: List of file paths, directories, URLs, or glob patterns
            project: Optional project identifier for filtering

        Returns:
            Tuple of (count, indexed_files) where indexed_files contains
            metadata for each file indexed (for database tracking)
        """
        files_data = self._collect_files(sources, project)
        if not files_data:
            return 0, []

        logger.info(f"Batch processing {len(files_data)} documentation files")
        results = await self.ingest_batch(self._build_documents(files_data))
        count = sum(1 for r in results if r.success)
        indexed_files = self._build_indexed_files(files_data)
        logger.info(f"Batch indexing complete: {count} docs indexed")
        return count, indexed_files

    def _detect_doc_type(self, file_path: Path) -> str:
        """Detect documentation type from filename."""
        name_lower = file_path.stem.lower()
        if "readme" in name_lower:
            return "readme"
        if "api" in name_lower:
            return "api"
        if "guide" in name_lower or "tutorial" in name_lower:
            return "guide"
        if "changelog" in name_lower:
            return "changelog"
        return "general"
