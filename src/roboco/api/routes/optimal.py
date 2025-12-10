"""
Optimal API Routes

Knowledge base, RAG queries, and semantic search endpoints.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from roboco.api.deps import CurrentAgentContext
from roboco.services.optimal import (
    IndexType,
    QueryContext,
    RAGResponse,
    SearchResult,
    get_optimal_service,
)

router = APIRouter(prefix="/optimal", tags=["optimal"])


# =============================================================================
# REQUEST/RESPONSE SCHEMAS
# =============================================================================


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


# =============================================================================
# INDEXING ENDPOINTS
# =============================================================================


@router.post("/kb/index/code", status_code=status.HTTP_201_CREATED)
async def index_code(
    request: IndexCodeRequest,
    agent: CurrentAgentContext,
) -> dict[str, Any]:
    """
    Index code files/directories.

    Indexes source code for semantic search. Supports:
    - Individual files
    - Directories
    - Glob patterns (e.g., "src/**/*.py")
    """
    service = await get_optimal_service()
    count = await service.index_code(
        sources=request.sources,
        project=request.project,
    )
    return {
        "indexed": count,
        "sources": request.sources,
        "project": request.project,
    }


@router.post("/kb/index/docs", status_code=status.HTTP_201_CREATED)
async def index_documentation(
    request: IndexDocsRequest,
    agent: CurrentAgentContext,
) -> dict[str, Any]:
    """
    Index documentation files.

    Indexes markdown, text, and other documentation. Supports:
    - Local files and directories
    - URLs (single page or crawl with /**)
    - Glob patterns
    """
    service = await get_optimal_service()
    count = await service.index_documentation(
        sources=request.sources,
        project=request.project,
    )
    return {
        "indexed": count,
        "sources": request.sources,
        "project": request.project,
    }


# =============================================================================
# SEARCH ENDPOINTS
# =============================================================================


@router.post("/kb/search", response_model=SearchResponse)
async def search(
    request: SearchRequest,
    agent: CurrentAgentContext,
) -> SearchResponse:
    """
    Semantic search across the knowledge base.

    Returns relevant documents matching the query.
    """
    # Build query context
    index_types = None
    if request.index_types:
        try:
            index_types = [IndexType(t) for t in request.index_types]
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid index type: {e}",
            ) from e

    context = QueryContext(
        project=request.project,
        task_id=request.task_id,
        agent_id=agent.agent_id,
        index_types=index_types,
    )

    service = await get_optimal_service()
    results = await service.search(
        query=request.query,
        context=context,
        top_k=request.top_k,
    )

    return SearchResponse(
        results=[
            SearchResultResponse(
                content=r.content,
                source=r.source,
                score=r.score,
                index_type=r.index_type.value,
                metadata=r.metadata,
            )
            for r in results
        ],
        query=request.query,
        total=len(results),
    )


@router.get("/kb/similar")
async def find_similar(
    source: str,
    top_k: int = 5,
    agent: CurrentAgentContext = None,
) -> SearchResponse:
    """
    Find documents similar to a given source.

    Pass a file path or URL to find similar content.
    """
    service = await get_optimal_service()
    # Use the source content as the query
    results = await service.search(
        query=f"Find documents similar to: {source}",
        top_k=top_k,
    )

    return SearchResponse(
        results=[
            SearchResultResponse(
                content=r.content,
                source=r.source,
                score=r.score,
                index_type=r.index_type.value,
                metadata=r.metadata,
            )
            for r in results
        ],
        query=source,
        total=len(results),
    )


# =============================================================================
# RAG ENDPOINTS
# =============================================================================


@router.post("/rag/query", response_model=RAGQueryResponse)
async def rag_query(
    request: RAGQueryRequest,
    agent: CurrentAgentContext,
) -> RAGQueryResponse:
    """
    Query the knowledge base with RAG.

    Retrieves relevant context and generates an answer.
    """
    # Build query context
    index_types = None
    if request.index_types:
        try:
            index_types = [IndexType(t) for t in request.index_types]
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid index type: {e}",
            ) from e

    context = QueryContext(
        project=request.project,
        task_id=request.task_id,
        agent_id=agent.agent_id,
        index_types=index_types,
    )

    service = await get_optimal_service()
    response = await service.query(
        query=request.query,
        context=context,
        top_k=request.top_k,
    )

    return RAGQueryResponse(
        answer=response.answer,
        citations=[
            SearchResultResponse(
                content=c.content,
                source=c.source,
                score=c.score,
                index_type=c.index_type.value,
                metadata=c.metadata,
            )
            for c in response.citations
        ],
        query=response.query,
        context_used=response.context_used,
    )


@router.post("/rag/context")
async def get_context(
    request: RAGQueryRequest,
    agent: CurrentAgentContext,
) -> SearchResponse:
    """
    Get context for prompt augmentation without generating an answer.

    Useful when you want to use your own LLM with retrieved context.
    """
    # Build query context
    index_types = None
    if request.index_types:
        try:
            index_types = [IndexType(t) for t in request.index_types]
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid index type: {e}",
            ) from e

    context = QueryContext(
        project=request.project,
        task_id=request.task_id,
        agent_id=agent.agent_id,
        index_types=index_types,
    )

    service = await get_optimal_service()
    results = await service.search(
        query=request.query,
        context=context,
        top_k=request.top_k,
    )

    return SearchResponse(
        results=[
            SearchResultResponse(
                content=r.content,
                source=r.source,
                score=r.score,
                index_type=r.index_type.value,
                metadata=r.metadata,
            )
            for r in results
        ],
        query=request.query,
        total=len(results),
    )


# =============================================================================
# MANAGEMENT ENDPOINTS
# =============================================================================


@router.get("/stats", response_model=IndexStatsResponse)
async def get_stats(
    agent: CurrentAgentContext,
) -> IndexStatsResponse:
    """Get statistics about all indexes."""
    service = await get_optimal_service()
    stats = await service.get_stats()
    return IndexStatsResponse(
        initialized=stats.get("initialized", False),
        indexes=stats.get("indexes", {}),
    )


@router.delete("/kb/{index_type}")
async def clear_index(
    index_type: str,
    agent: CurrentAgentContext,
) -> dict[str, str]:
    """
    Clear a specific index.

    Warning: This permanently deletes all documents in the index.
    """
    try:
        idx_type = IndexType(index_type)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid index type: {e}",
        ) from e

    service = await get_optimal_service()
    await service.clear_index(idx_type)

    return {"status": "cleared", "index_type": index_type}


@router.post("/kb/refresh")
async def refresh_index(
    request: RefreshRequest,
    agent: CurrentAgentContext,
) -> dict[str, Any]:
    """
    Refresh an index with updated sources.

    Re-indexes the specified sources to pick up changes.
    """
    try:
        idx_type = IndexType(request.index_type)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid index type: {e}",
        ) from e

    service = await get_optimal_service()
    await service.refresh_index(idx_type, request.sources)

    return {
        "status": "refreshed",
        "index_type": request.index_type,
        "sources": request.sources,
    }
