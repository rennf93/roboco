"""
Reviewer Service

Code review assistant that synthesizes feedback from multiple sources:
- Coding standards (from STANDARDS index)
- Past review comments on similar code (from REVIEWS index)
- Security policies (from STANDARDS/security)
- Known error patterns (from ERRORS index)
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar, cast
from uuid import UUID

import structlog

from roboco.models.optimal import (
    CodeReviewRequest,
    CodeReviewResult,
    SearchResult,
)

logger = structlog.get_logger()


class ReviewCategory(Enum):
    """Categories of review feedback."""

    STANDARDS = "standards"
    SECURITY = "security"
    PERFORMANCE = "performance"
    MAINTAINABILITY = "maintainability"
    ERROR_PRONE = "error_prone"
    STYLE = "style"


class ReviewSeverity(Enum):
    """Severity of review comments."""

    BLOCKER = "blocker"  # Must fix before merge
    MAJOR = "major"  # Should fix
    MINOR = "minor"  # Nice to fix
    SUGGESTION = "suggestion"  # Optional improvement


@dataclass
class ReviewComment:
    """A single review comment."""

    category: ReviewCategory
    severity: ReviewSeverity
    message: str
    line_number: int | None = None
    suggestion: str | None = None
    rule_reference: str | None = None
    similar_past_comment: str | None = None


@dataclass
class ReviewSummary:
    """Summary of code review results."""

    file_path: str
    change_type: str
    comments: list[ReviewComment] = field(default_factory=list)
    blockers_count: int = 0
    major_count: int = 0
    minor_count: int = 0
    suggestions_count: int = 0
    overall_score: float = 100.0  # 0-100, deducted for issues
    approved: bool = True
    relevant_standards: list[SearchResult] = field(default_factory=list)
    past_reviews: list[SearchResult] = field(default_factory=list)


class ReviewerService:
    """
    Code review assistant that provides automated feedback.

    Searches across multiple knowledge indexes to provide
    comprehensive review feedback:
    - STANDARDS: Coding standards and best practices
    - REVIEWS: Past review comments on similar code
    - ERRORS: Known error patterns to watch for
    """

    # Score deductions by severity
    SEVERITY_DEDUCTIONS: ClassVar[dict[ReviewSeverity, int]] = {
        ReviewSeverity.BLOCKER: 25,
        ReviewSeverity.MAJOR: 10,
        ReviewSeverity.MINOR: 3,
        ReviewSeverity.SUGGESTION: 0,
    }

    # Similarity thresholds
    PAST_REVIEW_THRESHOLD: ClassVar[float] = 0.7
    ERROR_PATTERN_THRESHOLD: ClassVar[float] = 0.6

    def __init__(self) -> None:
        """Initialize ReviewerService."""
        self._optimal_service: Any = None

    async def initialize(self, optimal_service: Any) -> None:
        """
        Initialize with OptimalService reference.

        Args:
            optimal_service: The OptimalService instance
        """
        self._optimal_service = optimal_service
        logger.info("ReviewerService initialized")

    async def review_code(
        self,
        request: CodeReviewRequest,
    ) -> CodeReviewResult:
        """
        Review code and provide feedback.

        Args:
            request: CodeReviewRequest with code, file_path, and change_type

        Returns:
            CodeReviewResult with comments, score, and approval status
        """
        if not self._optimal_service:
            raise RuntimeError("ReviewerService not initialized")

        comments: list[ReviewComment] = []

        # 1. Get relevant coding standards
        standards = await self._get_relevant_standards(
            request.file_path,
            request.code,
        )

        # 2. Get past reviews for similar code
        past_reviews = await self._get_similar_reviews(
            request.file_path,
            request.code,
        )

        # 3. Check for known error patterns
        error_patterns = await self._check_error_patterns(request.code)

        # 4. Apply standards checks
        standards_comments = self._check_standards(
            request.code,
            standards,
            request.file_path,
        )
        comments.extend(standards_comments)

        # 5. Apply security checks
        security_comments = await self._check_security(
            request.code,
            request.file_path,
        )
        comments.extend(security_comments)

        # 6. Add comments from similar past reviews
        past_comments = self._apply_past_reviews(
            request.code,
            past_reviews,
        )
        comments.extend(past_comments)

        # 7. Add warnings for known error patterns
        error_comments = self._apply_error_patterns(
            request.code,
            error_patterns,
        )
        comments.extend(error_comments)

        # Build summary
        summary = self._build_summary(
            file_path=request.file_path,
            change_type=request.change_type,
            comments=comments,
            standards=standards,
            past_reviews=past_reviews,
        )

        return CodeReviewResult(
            file_path=request.file_path,
            approved=summary.approved,
            score=int(summary.overall_score),
            comments=[
                {
                    "category": c.category.value,
                    "severity": c.severity.value,
                    "message": c.message,
                    "line_number": c.line_number,
                    "suggestion": c.suggestion,
                    "rule_reference": c.rule_reference,
                }
                for c in comments
            ],
            standards_checked=[s.content[:100] for s in standards[:5]],
            similar_reviews=[r.content[:100] for r in past_reviews[:3]],
        )

    async def _get_relevant_standards(
        self,
        file_path: str,
        code: str,
    ) -> list[SearchResult]:
        """Get coding standards relevant to the file and code."""
        # Determine language from file extension
        language = self._detect_language(file_path)

        # Search for relevant standards
        standards: list[SearchResult] = cast(
            "list[SearchResult]",
            await self._optimal_service.get_standards(
                domain="coding",
                language=language,
            ),
        )

        # Also search for security standards if code looks sensitive
        if self._looks_security_sensitive(code):
            security_standards = cast(
                "list[SearchResult]",
                await self._optimal_service.get_standards(
                    domain="security",
                ),
            )
            standards.extend(security_standards)

        return standards

    async def _get_similar_reviews(
        self,
        file_path: str,
        _code: str,
    ) -> list[SearchResult]:
        """Get past review comments on similar code."""
        return cast(
            "list[SearchResult]",
            await self._optimal_service.get_reviews_for_file(
                file_path=file_path,
                top_k=5,
            ),
        )

    async def _check_error_patterns(
        self,
        code: str,
    ) -> list[SearchResult]:
        """Check for known error patterns in the code."""
        # Search errors index for patterns that match this code
        return cast(
            "list[SearchResult]",
            await self._optimal_service.search_errors(
                query=code[:500],  # Use first 500 chars as query
                top_k=5,
            ),
        )

    def _check_standards(
        self,
        code: str,
        standards: list[SearchResult],
        file_path: str,
    ) -> list[ReviewComment]:
        """Check code against coding standards."""
        comments: list[ReviewComment] = []
        language = self._detect_language(file_path)

        # Python-specific checks
        if language == "python":
            comments.extend(self._check_python_standards(code, standards))

        # TypeScript-specific checks
        elif language in ("typescript", "javascript"):
            comments.extend(self._check_typescript_standards(code, standards))

        return comments

    def _check_python_standards(
        self,
        code: str,
        _standards: list[SearchResult],
    ) -> list[ReviewComment]:
        """Apply Python-specific standard checks."""
        comments: list[ReviewComment] = []

        # Check for type hints on functions
        lines = code.split("\n")
        for i, line in enumerate(lines, 1):
            # Function without return type hint
            is_function_def = line.strip().startswith("def ") and "->" not in line
            if is_function_def and ":" in line:
                comments.append(
                    ReviewComment(
                        category=ReviewCategory.STANDARDS,
                        severity=ReviewSeverity.MAJOR,
                        message="Function missing return type hint",
                        line_number=i,
                        suggestion="Add return type annotation (e.g., -> None)",
                        rule_reference="PY-001",
                    )
                )

            # Bare except
            if "except:" in line and "except Exception" not in line:
                comments.append(
                    ReviewComment(
                        category=ReviewCategory.ERROR_PRONE,
                        severity=ReviewSeverity.BLOCKER,
                        message="Bare except clause catches all exceptions",
                        line_number=i,
                        suggestion="Catch specific exceptions",
                        rule_reference="PY-004",
                    )
                )

            # Print statement (should use logging)
            if "print(" in line and "# noqa" not in line:
                comments.append(
                    ReviewComment(
                        category=ReviewCategory.STANDARDS,
                        severity=ReviewSeverity.MINOR,
                        message="Use structured logging instead of print",
                        line_number=i,
                        suggestion="Replace with logger.info() or logger.debug()",
                        rule_reference="PY-005",
                    )
                )

        return comments

    def _check_typescript_standards(
        self,
        code: str,
        _standards: list[SearchResult],
    ) -> list[ReviewComment]:
        """Apply TypeScript-specific standard checks."""
        comments: list[ReviewComment] = []

        lines = code.split("\n")
        for i, line in enumerate(lines, 1):
            # any type usage
            if ": any" in line or "as any" in line:
                comments.append(
                    ReviewComment(
                        category=ReviewCategory.STANDARDS,
                        severity=ReviewSeverity.MAJOR,
                        message="Avoid using 'any' type",
                        line_number=i,
                        suggestion="Use a specific type or 'unknown'",
                        rule_reference="TS-001",
                    )
                )

            # console.log (should use proper logging)
            if "console.log(" in line and "// noqa" not in line:
                comments.append(
                    ReviewComment(
                        category=ReviewCategory.STANDARDS,
                        severity=ReviewSeverity.MINOR,
                        message="Remove console.log before committing",
                        line_number=i,
                        suggestion="Use proper logging or remove",
                    )
                )

        return comments

    async def _check_security(
        self,
        code: str,
        _file_path: str,
    ) -> list[ReviewComment]:
        """Check for security issues."""
        comments: list[ReviewComment] = []

        lines = code.split("\n")
        for i, line in enumerate(lines, 1):
            # SQL injection risk
            if "execute(" in line.lower() and 'f"' in line:
                comments.append(
                    ReviewComment(
                        category=ReviewCategory.SECURITY,
                        severity=ReviewSeverity.BLOCKER,
                        message="Potential SQL injection vulnerability",
                        line_number=i,
                        suggestion="Use parameterized queries",
                        rule_reference="SEC-001",
                    )
                )

            # Hardcoded secrets
            secret_patterns = ["password=", "api_key=", "secret=", "token="]
            line_lower = line.lower()
            for pattern in secret_patterns:
                if pattern in line_lower and ('"' in line or "'" in line):
                    comments.append(
                        ReviewComment(
                            category=ReviewCategory.SECURITY,
                            severity=ReviewSeverity.BLOCKER,
                            message=f"Possible hardcoded secret: {pattern.rstrip('=')}",
                            line_number=i,
                            suggestion="Use environment variables",
                            rule_reference="SEC-003",
                        )
                    )
                    break

            # eval() usage
            if "eval(" in line:
                comments.append(
                    ReviewComment(
                        category=ReviewCategory.SECURITY,
                        severity=ReviewSeverity.BLOCKER,
                        message="eval() is a security risk",
                        line_number=i,
                        suggestion="Avoid eval(); use safe alternatives",
                        rule_reference="SEC-007",
                    )
                )

        return comments

    def _apply_past_reviews(
        self,
        _code: str,
        past_reviews: list[SearchResult],
    ) -> list[ReviewComment]:
        """Apply relevant comments from past reviews."""
        comments: list[ReviewComment] = []

        for review in past_reviews[:3]:  # Limit to top 3 matches
            if review.score > self.PAST_REVIEW_THRESHOLD:
                comments.append(
                    ReviewComment(
                        category=ReviewCategory.MAINTAINABILITY,
                        severity=ReviewSeverity.SUGGESTION,
                        message="Similar code was reviewed before",
                        suggestion=review.content[:200],
                        similar_past_comment=review.source,
                    )
                )

        return comments

    def _apply_error_patterns(
        self,
        _code: str,
        error_patterns: list[SearchResult],
    ) -> list[ReviewComment]:
        """Warn about known error patterns."""
        comments: list[ReviewComment] = []

        for pattern in error_patterns[:3]:
            if pattern.score > self.ERROR_PATTERN_THRESHOLD:
                comments.append(
                    ReviewComment(
                        category=ReviewCategory.ERROR_PRONE,
                        severity=ReviewSeverity.MAJOR,
                        message="Similar code has caused errors before",
                        suggestion=f"Known issue: {pattern.content[:150]}...",
                    )
                )

        return comments

    def _build_summary(
        self,
        file_path: str,
        change_type: str,
        comments: list[ReviewComment],
        standards: list[SearchResult],
        past_reviews: list[SearchResult],
    ) -> ReviewSummary:
        """Build review summary from comments."""
        # Count by severity
        blockers = sum(1 for c in comments if c.severity == ReviewSeverity.BLOCKER)
        majors = sum(1 for c in comments if c.severity == ReviewSeverity.MAJOR)
        minors = sum(1 for c in comments if c.severity == ReviewSeverity.MINOR)
        suggestions = sum(
            1 for c in comments if c.severity == ReviewSeverity.SUGGESTION
        )

        # Calculate score
        score = 100.0
        for comment in comments:
            score -= self.SEVERITY_DEDUCTIONS.get(comment.severity, 0)
        score = max(0.0, score)

        # Determine approval
        approved = blockers == 0

        return ReviewSummary(
            file_path=file_path,
            change_type=change_type,
            comments=comments,
            blockers_count=blockers,
            major_count=majors,
            minor_count=minors,
            suggestions_count=suggestions,
            overall_score=score,
            approved=approved,
            relevant_standards=standards,
            past_reviews=past_reviews,
        )

    def _detect_language(self, file_path: str) -> str:
        """Detect programming language from file path."""
        ext_map = {
            ".py": "python",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".js": "javascript",
            ".jsx": "javascript",
            ".go": "go",
            ".rs": "rust",
            ".java": "java",
            ".rb": "ruby",
            ".php": "php",
            ".cs": "csharp",
            ".cpp": "cpp",
            ".c": "c",
            ".h": "c",
            ".hpp": "cpp",
            ".md": "markdown",
            ".sql": "sql",
            ".sh": "shell",
            ".bash": "shell",
            ".yml": "yaml",
            ".yaml": "yaml",
            ".json": "json",
        }

        for ext, lang in ext_map.items():
            if file_path.endswith(ext):
                return lang

        return "unknown"

    def _looks_security_sensitive(self, code: str) -> bool:
        """Check if code appears to handle sensitive operations."""
        sensitive_keywords = [
            "password",
            "secret",
            "token",
            "auth",
            "credential",
            "api_key",
            "private_key",
            "encrypt",
            "decrypt",
            "hash",
            "session",
            "cookie",
            "permission",
            "role",
            "admin",
        ]

        code_lower = code.lower()
        return any(kw in code_lower for kw in sensitive_keywords)

    async def get_review_history(
        self,
        file_path: str,
        limit: int = 10,
    ) -> list[SearchResult]:
        """Get review history for a file."""
        return cast(
            "list[SearchResult]",
            await self._optimal_service.get_reviews_for_file(
                file_path=file_path,
                top_k=limit,
            ),
        )

    async def record_review(
        self,
        file_path: str,
        reviewer_id: UUID,
        comments: list[dict[str, Any]],
        approved: bool,
    ) -> str:
        """
        Record a review for future reference.

        Args:
            file_path: Path to the reviewed file
            reviewer_id: ID of the reviewing agent
            comments: List of review comments
            approved: Whether the review was approved

        Returns:
            Review ID
        """
        from roboco.models.optimal import IndexReviewParams

        params = IndexReviewParams(
            file_path=file_path,
            reviewer_id=reviewer_id,
            comments=comments,
            approved=approved,
            summary=f"Review: {len(comments)} comments, "
            f"{'approved' if approved else 'changes requested'}",
        )

        return cast("str", await self._optimal_service.record_review(params))


class _ReviewerServiceHolder:
    """Holder for singleton ReviewerService instance."""

    instance: ReviewerService | None = None


async def get_reviewer_service() -> ReviewerService:
    """Get or create the ReviewerService instance."""
    if _ReviewerServiceHolder.instance is None:
        _ReviewerServiceHolder.instance = ReviewerService()
    return _ReviewerServiceHolder.instance
