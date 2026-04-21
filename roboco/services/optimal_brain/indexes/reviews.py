"""
Reviews Index Plugin

Handles indexing and searching code review feedback.
"""

from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from roboco.models.optimal import IndexType, SearchResult
from roboco.services.optimal_brain.indexes.base import BaseIndexPlugin, IngestResult


@dataclass
class RecordReviewParams:
    """Parameters for recording a code review comment."""

    comment: str
    file_path: str
    reviewer_id: UUID | None = None
    author_id: UUID | None = None
    task_id: UUID | None = None
    review_type: str = "code"
    severity: str = "info"
    line_number: int | None = None
    tags: list[str] = field(default_factory=list)


class ReviewsIndexPlugin(BaseIndexPlugin):
    """
    Plugin for indexing and searching code review feedback.

    Enables:
    - Learning from past review comments
    - Consistent code review standards
    - Pattern detection in feedback
    """

    @property
    def index_type(self) -> IndexType:
        return IndexType.REVIEWS

    def prepare_metadata(
        self,
        content: str,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Prepare metadata for review."""
        del content  # Unused - metadata comes from kwargs
        return {
            "type": "review",
            "file_path": kwargs.get("file_path", ""),
            "file_pattern": self._extract_pattern(kwargs.get("file_path", "")),
            "reviewer_id": str(kwargs.get("reviewer_id", "")),
            "author_id": str(kwargs.get("author_id", "")),
            "task_id": str(kwargs.get("task_id")) if kwargs.get("task_id") else "none",
            "review_type": kwargs.get(
                "review_type", "code"
            ),  # code, security, performance
            "severity": kwargs.get("severity", "info"),  # info, warning, error
            "tags": kwargs.get("tags", []),
        }

    def build_source_uri(
        self,
        doc_id: str | None = None,
        **kwargs: Any,
    ) -> str:
        """Build source URI for review."""
        del kwargs  # Unused - URI uses doc_id only
        review_id = doc_id or "rev-unknown"
        return f"roboco://reviews/{review_id}"

    def _extract_pattern(self, file_path: str) -> str:
        """
        Extract a pattern from file path for matching similar files.

        e.g., "src/api/routes/users.py" -> "api/routes/*.py"
        """
        if not file_path:
            return ""

        parts = file_path.split("/")
        min_path_parts = 2
        if len(parts) < min_path_parts:
            return file_path

        # Keep directory structure but wildcard the filename
        ext = ""
        if "." in parts[-1]:
            ext = "." + parts[-1].split(".")[-1]

        return "/".join(parts[:-1]) + "/*" + ext

    async def record_review(self, params: RecordReviewParams) -> IngestResult:
        """
        Record a code review comment.

        Args:
            params: RecordReviewParams containing:
                - comment: The review comment
                - file_path: Path to the file being reviewed
                - reviewer_id: ID of the reviewer
                - author_id: ID of the code author
                - task_id: Related task ID
                - review_type: Type of review (code, security, performance)
                - severity: Comment severity (info, warning, error)
                - line_number: Line number if applicable
                - tags: Additional tags

        Returns:
            IngestResult with ingestion details
        """
        import hashlib

        # Generate review ID
        review_hash = hashlib.md5(
            f"{params.file_path}{params.comment[:50]}".encode(),
            usedforsecurity=False,
        ).hexdigest()[:12]
        review_id = f"rev-{review_hash}"

        # Build content
        content_parts = [f"File: {params.file_path}"]
        if params.line_number:
            content_parts.append(f"Line: {params.line_number}")
        content_parts.append(f"Type: {params.review_type}")
        content_parts.append(f"Severity: {params.severity}")
        content_parts.append(f"Comment: {params.comment}")

        if params.tags:
            content_parts.append(f"Tags: {', '.join(params.tags)}")

        content = "\n".join(content_parts)

        return await self.ingest(
            content=content,
            doc_id=review_id,
            file_path=params.file_path,
            reviewer_id=params.reviewer_id,
            author_id=params.author_id,
            task_id=params.task_id,
            review_type=params.review_type,
            severity=params.severity,
            tags=params.tags,
        )

    async def get_reviews_for_file(
        self,
        file_path: str,
        top_k: int = 10,
    ) -> list[SearchResult]:
        """
        Get past reviews for a file or similar files.

        Uses file pattern matching to find relevant reviews.
        """
        pattern = self._extract_pattern(file_path)
        query = f"Review for file: {file_path}"

        # Search without filters first
        outcome = await self.search(query=query, top_k=top_k * 2)
        results = outcome.results

        # Filter by pattern similarity
        filtered = []
        for result in results:
            result_pattern = result.metadata.get("file_pattern", "")
            if (
                pattern
                and result_pattern
                and (pattern in result_pattern or result_pattern in pattern)
            ) or file_path in result.content:
                filtered.append(result)

        return filtered[:top_k] if filtered else results[:top_k]

    async def search_by_type(
        self,
        query: str,
        review_type: str,
        top_k: int = 5,
    ) -> list[SearchResult]:
        """Search reviews by type (code, security, performance)."""
        outcome = await self.search(
            query=query,
            top_k=top_k,
            filters={"review_type": review_type},
        )
        return outcome.results

    async def search_by_severity(
        self,
        query: str,
        severity: str,
        top_k: int = 5,
    ) -> list[SearchResult]:
        """Search reviews by severity."""
        outcome = await self.search(
            query=query,
            top_k=top_k,
            filters={"severity": severity},
        )
        return outcome.results
