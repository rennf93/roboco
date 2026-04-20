"""
Optimal API Schemas

Request/response models for knowledge base and RAG endpoints.
"""

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class IndexCodeRequest(BaseModel):
    """Request to index code files."""

    sources: list[str] = Field(
        ..., min_length=1, description="File paths, directories, or globs"
    )
    project: str | None = Field(None, description="Project identifier for filtering")


class IndexDocsRequest(BaseModel):
    """Request to index documentation."""

    sources: list[str] = Field(
        ..., min_length=1, description="File paths, URLs, or globs"
    )
    project: str | None = Field(None, description="Project identifier for filtering")


class SearchRequest(BaseModel):
    """Request for semantic search."""

    query: str = Field(..., min_length=1, description="Natural language query")
    project: str | None = Field(None, description="Filter by project")
    task_id: UUID | None = Field(None, description="Filter by task")
    index_types: list[str] | None = Field(None, description="Index types to search")
    top_k: int = Field(5, ge=1, le=20, description="Number of results")


class RAGQueryRequest(BaseModel):
    """Request for RAG query."""

    query: str = Field(..., min_length=1, description="Natural language question")
    project: str | None = Field(None, description="Filter by project")
    task_id: UUID | None = Field(None, description="Filter by task")
    index_types: list[str] | None = Field(None, description="Index types to query")
    top_k: int = Field(5, ge=1, le=20, description="Context chunks to use")


class SearchResultResponse(BaseModel):
    """A single search result."""

    content: str
    source: str
    score: float
    index_type: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResponse(BaseModel):
    """Response from semantic search."""

    results: list[SearchResultResponse]
    query: str
    total: int


class RAGQueryResponse(BaseModel):
    """Response from RAG query."""

    answer: str
    citations: list[SearchResultResponse]
    query: str
    context_used: int
    search_stats: dict[str, int] | None = Field(
        default=None, description="Results count per index (-1 = error)"
    )
    search_errors: dict[str, str] | None = Field(
        default=None, description="Error messages for failed indexes"
    )


class IndexStatsResponse(BaseModel):
    """Statistics for all indexes."""

    initialized: bool
    indexes: dict[str, dict[str, Any]]


class SingleIndexStatsResponse(BaseModel):
    """Statistics for a single index."""

    index_type: str
    document_count: int
    chunk_count: int
    last_updated: str | None = None


class RAGHealthResponse(BaseModel):
    """Response from RAG health check."""

    healthy: bool
    embedding_status: str = Field(..., description="Embedding model status")
    llm_status: str = Field(..., description="LLM (HyDE) status")
    vector_store_status: str = Field(..., description="Vector store status")
    details: dict[str, Any] = Field(default_factory=dict)


class RefreshRequest(BaseModel):
    """Request to refresh an index."""

    index_type: str = Field(..., description="Index type to refresh")
    # Empty / omitted list means "refresh every source currently registered
    # for this index" — the refresh route discovers them from
    # `indexed_documents`. This is what the panel's Refresh All button
    # sends, which was rejecting with a 422 under the old min_length=1.
    sources: list[str] = Field(
        default_factory=list,
        description="Sources to refresh (empty = all sources in this index)",
    )


class IndexResponse(BaseModel):
    """Response from indexing operations."""

    indexed: int
    sources: list[str]
    project: str | None


class DocumentListItem(BaseModel):
    """A document in an index."""

    id: str
    source: str
    indexed_at: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class PaginationParams(BaseModel):
    """Pagination query parameters."""

    limit: int = Field(50, ge=1, le=100, description="Max items to return")
    offset: int = Field(0, ge=0, description="Skip items")


class DocumentListResponse(BaseModel):
    """Response from listing documents in an index."""

    documents: list[DocumentListItem]
    total: int
    index_type: str


class ClearIndexResponse(BaseModel):
    """Response from clearing an index."""

    status: str
    index_type: str


class RefreshIndexResponse(BaseModel):
    """Response from refreshing an index."""

    status: str
    index_type: str
    sources: list[str]


class PromptTemplateRequest(BaseModel):
    """Request to create/manage a prompt template."""

    name: str = Field(..., min_length=1, max_length=100, description="Template name")
    template: str = Field(..., min_length=1, description="Prompt template")
    description: str | None = Field(None, description="Template description")
    variables: list[str] = Field(default_factory=list, description="Variables")
    category: str | None = Field(None, description="Template category")


class PromptTemplateResponse(BaseModel):
    """Response for prompt template."""

    id: str
    name: str
    template: str
    description: str | None
    variables: list[str]
    category: str | None
    created_at: str


class TokenEstimateRequest(BaseModel):
    """Request to estimate token count."""

    content: str = Field(..., min_length=1, description="Content to estimate")
    model: str = Field("claude-sonnet-4-20250514", description="Model")


class TokenEstimateResponse(BaseModel):
    """Response with token count estimate."""

    token_count: int
    model: str
    content_length: int


# =============================================================================
# OPTIMAL BRAIN SCHEMAS
# =============================================================================


# Mentor Schemas
class MentorAskRequest(BaseModel):
    """Request to ask the mentor a question."""

    question: str = Field(..., min_length=1, description="Question to ask")
    conversation_id: str | None = Field(None, description="Continue conversation")
    domain: str | None = Field(
        None, description="Domain filter (coding, security, workflow)"
    )


class MentorAskResponse(BaseModel):
    """Response from mentor."""

    answer: str
    sources: list[SearchResultResponse]
    conversation_id: str
    suggested_followups: list[str]
    search_stats: dict[str, int] | None = Field(
        default=None, description="Results count per index (-1 = error)"
    )
    search_errors: dict[str, str] | None = Field(
        default=None, description="Error messages for failed indexes"
    )


# Error Schemas
class ErrorSearchRequest(BaseModel):
    """Request to search for error solutions."""

    error_message: str = Field(..., min_length=1, description="Error message")
    context: str = Field("", description="Additional context")


class ErrorSearchResponse(BaseModel):
    """Response from error search."""

    results: list[SearchResultResponse]
    total: int


class ErrorRecordRequest(BaseModel):
    """Request to record an error solution."""

    error_message: str = Field(..., min_length=1, description="Error message")
    context: str = Field(..., description="Context when error occurred")
    solution: str = Field(..., min_length=1, description="How it was fixed")
    worked: bool = Field(True, description="Whether the solution worked")
    tags: list[str] = Field(default_factory=list, description="Tags")


class ErrorRecordResponse(BaseModel):
    """Response from recording an error."""

    error_id: str
    status: str


# Decision Schemas
class DecisionCheckRequest(BaseModel):
    """Request to check for decision precedents."""

    topic: str = Field(..., min_length=1, description="Decision topic")


class DecisionCheckResponse(BaseModel):
    """Response from decision check."""

    has_precedent: bool
    decisions: list[dict[str, Any]]
    recommendation: str


class DecisionRecordRequest(BaseModel):
    """Request to record a decision."""

    topic: str = Field(..., min_length=1, description="Decision topic")
    decision: str = Field(..., min_length=1, description="The decision made")
    rationale: str = Field(..., min_length=1, description="Why this decision")
    alternatives: list[dict[str, Any]] = Field(
        default_factory=list, description="Alternatives considered"
    )
    context: str = Field("", description="Additional context")
    scope: str = Field("team", description="Scope: team or org")
    tags: list[str] = Field(default_factory=list, description="Tags")


class DecisionRecordResponse(BaseModel):
    """Response from recording a decision."""

    decision_id: str
    status: str


# Standards Schemas
class StandardsGetRequest(BaseModel):
    """Request to get standards."""

    domain: str = Field(..., description="Domain: coding, security, workflow")
    language: str | None = Field(None, description="Language filter")


class StandardsGetResponse(BaseModel):
    """Response with standards."""

    standards: list[SearchResultResponse]
    total: int


class ValidateActionRequest(BaseModel):
    """Request to validate an action against standards."""

    action_type: str = Field(..., description="Type of action")
    context: str = Field(..., description="Action details/code")


class ValidateActionResponse(BaseModel):
    """Response from action validation."""

    allowed: bool
    violations: list[dict[str, Any]]
    warnings: list[dict[str, Any]]
    relevant_standards: list[SearchResultResponse]


# Code Review Schemas
class CodeReviewRequest(BaseModel):
    """Request to review code."""

    code: str = Field(..., min_length=1, description="Code to review")
    file_path: str = Field(..., min_length=1, description="File path being reviewed")
    change_type: str = Field("modify", description="Change type: add, modify, delete")


class CodeReviewResponse(BaseModel):
    """Response from code review."""

    file_path: str
    approved: bool
    score: float = Field(ge=0, le=100, description="Review score 0-100")
    comments: list[dict[str, Any]]
    standards_checked: list[str]
    similar_reviews: list[str]


# Learning Schemas
class LearningRecordRequest(BaseModel):
    """Request to record a learning."""

    content: str = Field(..., min_length=1, description="Learning content")
    category: str = Field(
        ...,
        min_length=1,
        description="Category: error_handling, performance, testing, pattern, etc.",
    )
    team: str | None = Field(None, description="Team: backend, frontend, ux_ui")
    shareable: bool = Field(True, description="Share with other agents?")
    tags: list[str] = Field(default_factory=list, description="Tags")


class LearningRecordResponse(BaseModel):
    """Response from recording a learning."""

    learning_id: str
    status: str


class LearningSearchRequest(BaseModel):
    """Request to search learnings."""

    query: str = Field(..., min_length=1, description="Search query")
    category: str | None = Field(None, description="Category filter")
    team: str | None = Field(None, description="Team filter")
    top_k: int = Field(10, ge=1, le=50, description="Number of results")


# =============================================================================
# Proactive Context Schemas
# =============================================================================


class ProactiveContextRequest(BaseModel):
    """Request to fetch proactive context for a task."""

    task_id: UUID = Field(..., description="Task ID to get context for")


class ProactiveContextItem(BaseModel):
    """A single proactive context item."""

    content: str = Field(..., description="Context content")
    source: str = Field(..., description="Source of this context")
    score: float = Field(..., description="Relevance score")
    index_type: str = Field(..., description="Type of index this came from")
    metadata: dict[str, Any] = Field(
        default_factory=dict, description="Additional metadata"
    )


class ProactiveContextResponse(BaseModel):
    """Response with proactive context for a task."""

    task_id: UUID | None = Field(
        None, description="Task ID (if context is task-specific)"
    )
    agent_id: UUID | None = Field(None, description="Agent ID")
    similar_tasks: list[ProactiveContextItem] = Field(
        default_factory=list, description="Similar past tasks"
    )
    relevant_learnings: list[ProactiveContextItem] = Field(
        default_factory=list, description="Relevant learnings from past work"
    )
    code_patterns: list[ProactiveContextItem] = Field(
        default_factory=list, description="Relevant code patterns"
    )
    applicable_standards: list[ProactiveContextItem] = Field(
        default_factory=list, description="Applicable standards and rules"
    )
    recent_decisions: list[ProactiveContextItem] = Field(
        default_factory=list, description="Recent relevant decisions"
    )
    known_issues: list[ProactiveContextItem] = Field(
        default_factory=list, description="Known issues that may apply"
    )
    summary: str = Field("", description="Summary of the context package")
