"""
Optimal API Routes

Knowledge base, RAG queries, and semantic search endpoints.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status

from roboco.api.deps import CurrentAgentContext
from roboco.api.schemas.optimal import (
    ClearIndexResponse,
    IndexCodeRequest,
    IndexDocsRequest,
    IndexResponse,
    IndexStatsResponse,
    PromptTemplateRequest,
    PromptTemplateResponse,
    RAGQueryRequest,
    RAGQueryResponse,
    RefreshIndexResponse,
    RefreshRequest,
    SearchRequest,
    SearchResponse,
    SearchResultResponse,
    TokenEstimateRequest,
    TokenEstimateResponse,
)
from roboco.models import AgentRole
from roboco.services.optimal import (
    IndexType,
    QueryContext,
    get_optimal_service,
)

router = APIRouter()


# =============================================================================
# INDEXING ENDPOINTS
# =============================================================================


@router.post(
    "/kb/index/code",
    response_model=IndexResponse,
    status_code=status.HTTP_201_CREATED,
)
async def index_code(
    request: IndexCodeRequest,
    agent: CurrentAgentContext,
) -> IndexResponse:
    """
    Index code files/directories.

    Indexes source code for semantic search. Supports:
    - Individual files
    - Directories
    - Glob patterns (e.g., "src/**/*.py")
    """
    # Only developers and PMs can index code
    allowed = {AgentRole.DEVELOPER, AgentRole.CELL_PM, AgentRole.MAIN_PM, AgentRole.CEO}
    if agent.role not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to index code",
        )

    service = await get_optimal_service()
    count = await service.index_code(
        sources=request.sources,
        project=request.project,
    )
    return IndexResponse(
        indexed=count,
        sources=request.sources,
        project=request.project,
    )


@router.post(
    "/kb/index/docs",
    response_model=IndexResponse,
    status_code=status.HTTP_201_CREATED,
)
async def index_documentation(
    request: IndexDocsRequest,
    agent: CurrentAgentContext,
) -> IndexResponse:
    """
    Index documentation files.

    Indexes markdown, text, and other documentation. Supports:
    - Local files and directories
    - URLs (single page or crawl with /**)
    - Glob patterns
    """
    # Documenters and above can index docs
    allowed = {
        AgentRole.DOCUMENTER,
        AgentRole.DEVELOPER,
        AgentRole.CELL_PM,
        AgentRole.MAIN_PM,
        AgentRole.CEO,
    }
    if agent.role not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to index documentation",
        )

    service = await get_optimal_service()
    count = await service.index_documentation(
        sources=request.sources,
        project=request.project,
    )
    return IndexResponse(
        indexed=count,
        sources=request.sources,
        project=request.project,
    )


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


@router.get("/kb/similar", response_model=SearchResponse)
async def find_similar(
    source: str,
    agent: CurrentAgentContext,
    top_k: int = 5,
) -> SearchResponse:
    """
    Find documents similar to a given source.

    Pass a file path or URL to find similar content.
    """
    context = QueryContext(agent_id=agent.agent_id)

    service = await get_optimal_service()
    results = await service.search(
        query=f"Find documents similar to: {source}",
        context=context,
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
    # PMs and above can view stats
    allowed = {AgentRole.CELL_PM, AgentRole.MAIN_PM, AgentRole.CEO, AgentRole.AUDITOR}
    if agent.role not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view index statistics",
        )

    service = await get_optimal_service()
    stats = await service.get_stats()
    return IndexStatsResponse(
        initialized=stats.get("initialized", False),
        indexes=stats.get("indexes", {}),
    )


@router.delete("/kb/{index_type}", response_model=ClearIndexResponse)
async def clear_index(
    index_type: str,
    agent: CurrentAgentContext,
) -> ClearIndexResponse:
    """
    Clear a specific index.

    Warning: This permanently deletes all documents in the index.
    """
    # Only Main PM and CEO can clear indexes (destructive operation)
    allowed = {AgentRole.MAIN_PM, AgentRole.CEO}
    if agent.role not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to clear indexes",
        )

    try:
        idx_type = IndexType(index_type)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid index type: {e}",
        ) from e

    service = await get_optimal_service()
    await service.clear_index(idx_type)

    return ClearIndexResponse(status="cleared", index_type=index_type)


@router.post("/kb/refresh", response_model=RefreshIndexResponse)
async def refresh_index(
    request: RefreshRequest,
    agent: CurrentAgentContext,
) -> RefreshIndexResponse:
    """
    Refresh an index with updated sources.

    Re-indexes the specified sources to pick up changes.
    """
    # Developers and PMs can refresh indexes
    allowed = {AgentRole.DEVELOPER, AgentRole.CELL_PM, AgentRole.MAIN_PM, AgentRole.CEO}
    if agent.role not in allowed:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to refresh indexes",
        )

    try:
        idx_type = IndexType(request.index_type)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid index type: {e}",
        ) from e

    service = await get_optimal_service()
    await service.refresh_index(idx_type, request.sources)

    return RefreshIndexResponse(
        status="refreshed",
        index_type=request.index_type,
        sources=request.sources,
    )


# =============================================================================
# PROMPT TEMPLATE STORAGE
# =============================================================================


class _PromptTemplateStorageHolder:
    """Holder for prompt template storage (would be database in production)."""

    templates: dict[str, dict[str, Any]] | None = None


def _get_prompt_templates() -> dict[str, dict[str, Any]]:
    """Get the prompt templates storage."""
    if _PromptTemplateStorageHolder.templates is None:
        _PromptTemplateStorageHolder.templates = {}
    return _PromptTemplateStorageHolder.templates


def reset_prompt_templates() -> None:
    """Reset prompt templates (for testing)."""
    _PromptTemplateStorageHolder.templates = {}


# =============================================================================
# PROMPT TEMPLATE ENDPOINTS
# =============================================================================


@router.post(
    "/prompts",
    response_model=PromptTemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_prompt_template(
    request: PromptTemplateRequest,
    agent: CurrentAgentContext,
) -> PromptTemplateResponse:
    """
    Create a reusable prompt template.

    Templates can include {variables} that get substituted when rendering.
    """
    # Any authenticated agent can create prompt templates
    template_id = str(uuid4())
    created_at = datetime.now(UTC).isoformat()

    templates = _get_prompt_templates()
    templates[template_id] = {
        "id": template_id,
        "name": request.name,
        "template": request.template,
        "description": request.description,
        "variables": request.variables,
        "category": request.category,
        "created_at": created_at,
        "created_by": str(agent.agent_id),
    }

    return PromptTemplateResponse(
        id=template_id,
        name=request.name,
        template=request.template,
        description=request.description,
        variables=request.variables,
        category=request.category,
        created_at=created_at,
    )


@router.get("/prompts", response_model=list[PromptTemplateResponse])
async def list_prompt_templates(
    agent: CurrentAgentContext,
    category: str | None = None,
) -> list[PromptTemplateResponse]:
    """List all prompt templates, optionally filtered by category."""
    # Any authenticated agent can list templates
    _ = agent  # Used for authentication
    templates = list(_get_prompt_templates().values())

    if category:
        templates = [t for t in templates if t.get("category") == category]

    return [
        PromptTemplateResponse(
            id=t["id"],
            name=t["name"],
            template=t["template"],
            description=t["description"],
            variables=t["variables"],
            category=t["category"],
            created_at=t["created_at"],
        )
        for t in templates
    ]


# =============================================================================
# TOKEN ESTIMATION ENDPOINTS
# =============================================================================


@router.post("/tokens/estimate", response_model=TokenEstimateResponse)
async def estimate_tokens(
    request: TokenEstimateRequest,
    agent: CurrentAgentContext,
) -> TokenEstimateResponse:
    """
    Estimate token count for content.

    Uses a simple character-based estimation (avg 4 chars per token for English).
    For exact counts, use the Anthropic tokenizer directly.
    """
    # Any authenticated agent can estimate tokens
    _ = agent  # Used for authentication
    content_length = len(request.content)
    estimated_tokens = max(1, content_length // 4)

    return TokenEstimateResponse(
        token_count=estimated_tokens,
        model=request.model,
        content_length=content_length,
    )
