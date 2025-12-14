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


class IndexStatsResponse(BaseModel):
    """Statistics for all indexes."""

    initialized: bool
    indexes: dict[str, dict[str, Any]]


class RefreshRequest(BaseModel):
    """Request to refresh an index."""

    index_type: str = Field(..., description="Index type to refresh")
    sources: list[str] = Field(..., min_length=1, description="Sources to refresh")


class IndexResponse(BaseModel):
    """Response from indexing operations."""

    indexed: int
    sources: list[str]
    project: str | None


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
