"""
Optimal (RAG) Models

Data classes for knowledge base and RAG operations.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID


class IndexType(StrEnum):
    """Types of content indexes in the Optimal Brain.

    ``errors``: error patterns and their solutions. ``standards``: coding
    standards, security policies and workflow rules. ``decisions``:
    architectural and design decisions. ``reviews``: code review feedback.
    ``learnings``: cross-agent learnings. ``playbooks``: curated,
    Auditor-approved reusable procedures. ``vault_notes``: the CEO's own
    Obsidian vault notes. ``code`` is retired (see
    ``SEARCHABLE_INDEX_TYPES``).
    """

    CODE = "code"
    DOCUMENTATION = "documentation"
    JOURNALS = "journals"
    ERRORS = "errors"
    STANDARDS = "standards"
    DECISIONS = "decisions"
    REVIEWS = "reviews"
    LEARNINGS = "learnings"
    PLAYBOOKS = "playbooks"
    VAULT_NOTES = "vault_notes"


# The indexes a live plugin serves (roboco.services.optimal.PLUGIN_REGISTRY
# registers exactly these): what kb search, stats and clear can act on and
# the only names any agent-facing tool may advertise. CODE stays an enum
# member but is retired (slow CPU embedding, poor retrieval); the old
# "conversations" index went with the channels/messages subsystem (#306).
SEARCHABLE_INDEX_TYPES: tuple[IndexType, ...] = (
    IndexType.DOCUMENTATION,
    IndexType.JOURNALS,
    IndexType.ERRORS,
    IndexType.STANDARDS,
    IndexType.DECISIONS,
    IndexType.REVIEWS,
    IndexType.LEARNINGS,
    IndexType.PLAYBOOKS,
    IndexType.VAULT_NOTES,
)


@dataclass
class SearchResult:
    """A single search result from the knowledge base."""

    content: str
    source: str
    score: float
    index_type: IndexType
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchOutcome:
    """Result of a search operation with error status.

    Used to distinguish between "no results found" (success=True, results=[])
    and "search failed" (success=False, error_message set).
    """

    results: list[SearchResult]
    success: bool
    error_message: str | None = None
    index_type: IndexType | None = None
    search_time_ms: float = 0.0


@dataclass
class RAGResponse:
    """Response from a RAG query.

    ``context_used`` counts the context chunks used; ``search_stats`` holds
    the results per index, ``search_errors`` the errors per index and
    ``gaps`` the indexes that timed out.
    """

    answer: str
    citations: list[SearchResult]
    query: str
    context_used: int
    search_stats: dict[str, int] = field(default_factory=dict)
    search_errors: dict[str, str] = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)


@dataclass
class QueryContext:
    """Context for filtering RAG queries."""

    project: str | None = None
    task_id: UUID | None = None
    agent_id: UUID | None = None
    channel_id: UUID | None = None
    index_types: list[IndexType] | None = None


@dataclass
class IndexJournalEntryParams:
    """Parameters for indexing a journal entry."""

    content: str
    entry_type: str
    entry_id: UUID  # required; lifecycle events use a different indexing path
    agent_id: UUID | None = None
    task_id: UUID | None = None
    tags: list[str] | None = None


# =========================================================================
# Optimal Brain - New Models
# =========================================================================


@dataclass
class ErrorPattern:
    """An error pattern with its solution."""

    error_id: str
    error_message: str
    context: str
    solution: str
    worked: bool = True
    agent_id: UUID | None = None
    task_id: UUID | None = None
    team: str | None = None  # backend, frontend, ux_ui
    tags: list[str] = field(default_factory=list)


@dataclass
class IndexErrorParams:
    """Parameters for indexing an error pattern."""

    error_message: str
    context: str
    solution: str
    worked: bool = True
    agent_id: UUID | None = None
    task_id: UUID | None = None
    team: str | None = None
    tags: list[str] | None = None


@dataclass
class Decision:
    """An architectural or design decision."""

    decision_id: str
    topic: str
    decision: str
    rationale: str
    # List of alternatives: [{name, pros, cons}]
    alternatives: list[dict[str, Any]] = field(default_factory=list)
    context: str = ""
    agent_id: UUID | None = None
    task_id: UUID | None = None
    scope: str = "team"  # team, org
    tags: list[str] = field(default_factory=list)


@dataclass
class IndexDecisionParams:
    """Parameters for indexing a decision."""

    topic: str
    decision: str
    rationale: str
    alternatives: list[dict[str, Any]] | None = None
    context: str = ""
    agent_id: UUID | None = None
    task_id: UUID | None = None
    scope: str = "team"
    tags: list[str] | None = None


@dataclass
class Standard:
    """A coding/security/workflow standard."""

    standard_id: str
    domain: str  # coding, security, workflow, architecture
    title: str
    content: str
    language: str | None = None  # python, typescript, etc.
    severity: str = "recommended"  # required, recommended, optional
    tags: list[str] = field(default_factory=list)
    source_file: str | None = None  # Path to the .md file


@dataclass
class IndexStandardParams:
    """Parameters for indexing a standard."""

    domain: str
    title: str
    content: str
    language: str | None = None
    scope: str | None = None
    severity: str = "recommended"
    tags: list[str] | None = None
    source_file: str | None = None


@dataclass
class IndexReviewParams:
    """Parameters for indexing a code review."""

    file_path: str
    comments: list[dict[str, Any]]
    approved: bool
    summary: str
    reviewer_id: UUID | None = None
    author_id: UUID | None = None
    task_id: UUID | None = None


@dataclass
class ValidationResult:
    """Result of validating an action against standards."""

    allowed: bool
    # List of violations: [{rule_id, title, description}]
    violations: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    # Populated with raw SearchResult rows from the knowledge-base
    # index so the API layer can surface source + score + index_type +
    # metadata. Previously typed as `Standard` but that dataclass has
    # no retrieval metadata — the caller passes SearchResult.
    relevant_standards: list[SearchResult] = field(default_factory=list)


@dataclass
class MentorConversation:
    """A mentor conversation with memory."""

    conversation_id: str
    agent_id: UUID
    # List of turns: [{role, content, timestamp}]
    turns: list[dict[str, Any]] = field(default_factory=list)
    domain: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass
class MentorResponse:
    """Response from the mentor system."""

    answer: str
    sources: list[SearchResult] = field(default_factory=list)
    conversation_id: str = ""
    suggested_followups: list[str] = field(default_factory=list)
    search_stats: dict[str, int] = field(default_factory=dict)  # Results per index
    search_errors: dict[str, str] = field(default_factory=dict)  # Errors per index
    # Personalization context (what makes mentor different from ask)
    agent_role: str | None = None  # Detected role (developer, qa, etc.)
    agent_team: str | None = None  # Detected team (backend, frontend, etc.)
    journal_entries_used: int = 0  # Number of personal journal entries used


@dataclass
class CodeReviewRequest:
    """Request for code review assistance."""

    code: str
    file_path: str
    change_type: str  # new_file, modification, refactor
    context: str | None = None
    language: str | None = None


@dataclass
class CodeReviewResult:
    """Result of AI-assisted code review."""

    file_path: str
    approved: bool
    score: int  # 0-100
    comments: list[dict[str, Any]] = field(default_factory=list)
    standards_checked: list[str] = field(default_factory=list)
    similar_reviews: list[str] = field(default_factory=list)
