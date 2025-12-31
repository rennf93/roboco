"""
Optimal (RAG) Models

Data classes for knowledge base and RAG operations.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from uuid import UUID


class IndexType(str, Enum):
    """Types of content indexes in the Optimal Brain."""

    # Existing indexes
    CODE = "code"
    DOCUMENTATION = "documentation"
    CONVERSATIONS = "conversations"
    JOURNALS = "journals"

    # New indexes for Optimal Brain
    ERRORS = "errors"  # Error patterns and solutions
    STANDARDS = "standards"  # Coding standards, security policies, workflow rules
    DECISIONS = "decisions"  # Architectural and design decisions
    REVIEWS = "reviews"  # Code review feedback
    LEARNINGS = "learnings"  # Cross-agent learnings (shareable)


@dataclass
class SearchResult:
    """A single search result from the knowledge base."""

    content: str
    source: str
    score: float
    index_type: IndexType
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RAGResponse:
    """Response from a RAG query."""

    answer: str
    citations: list[SearchResult]
    query: str
    context_used: int  # Number of context chunks used


@dataclass
class QueryContext:
    """Context for filtering RAG queries."""

    project: str | None = None
    task_id: UUID | None = None
    agent_id: UUID | None = None
    channel_id: UUID | None = None
    index_types: list[IndexType] | None = None


@dataclass
class IndexConversationParams:
    """Parameters for indexing a conversation message."""

    content: str
    channel_id: UUID
    session_id: UUID
    agent_id: UUID
    task_id: UUID | None = None
    message_type: str | None = None


@dataclass
class IndexJournalEntryParams:
    """Parameters for indexing a journal entry.

    Note: entry_id and agent_id can be None for system events (e.g., lifecycle events).
    """

    content: str
    entry_type: str
    entry_id: UUID | None = None
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
    relevant_standards: list[Standard] = field(default_factory=list)


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


@dataclass
class ProactiveContext:
    """Context package for proactive knowledge injection."""

    similar_tasks: list[dict[str, Any]] = field(default_factory=list)
    learnings: list[dict[str, Any]] = field(default_factory=list)
    patterns: list[dict[str, Any]] = field(default_factory=list)
    standards: list[Standard] = field(default_factory=list)
    recent_decisions: list[Decision] = field(default_factory=list)
    suggested_approach: str = ""


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
