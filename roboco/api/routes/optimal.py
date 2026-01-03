"""
Optimal API Routes

Knowledge base, RAG queries, and semantic search endpoints.
"""

import hashlib
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, status

from roboco.api.deps import (
    CurrentAgentContext,
    DbSession,
    PaginationDep,
    PermissionServiceDep,
)
from roboco.api.schemas.optimal import (
    ClearIndexResponse,
    CodeReviewRequest,
    CodeReviewResponse,
    DecisionCheckRequest,
    DecisionCheckResponse,
    DecisionRecordRequest,
    DecisionRecordResponse,
    DocumentListItem,
    DocumentListResponse,
    ErrorRecordRequest,
    ErrorRecordResponse,
    ErrorSearchRequest,
    ErrorSearchResponse,
    IndexCodeRequest,
    IndexDocsRequest,
    IndexResponse,
    IndexStatsResponse,
    LearningRecordRequest,
    LearningRecordResponse,
    LearningSearchRequest,
    MentorAskRequest,
    MentorAskResponse,
    ProactiveContextItem,
    ProactiveContextRequest,
    ProactiveContextResponse,
    PromptTemplateRequest,
    PromptTemplateResponse,
    RAGHealthResponse,
    RAGQueryRequest,
    RAGQueryResponse,
    RefreshIndexResponse,
    RefreshRequest,
    SearchRequest,
    SearchResponse,
    SearchResultResponse,
    SingleIndexStatsResponse,
    StandardsGetRequest,
    StandardsGetResponse,
    TokenEstimateRequest,
    TokenEstimateResponse,
    ValidateActionRequest,
    ValidateActionResponse,
)
from roboco.models.optimal import (
    CodeReviewRequest as ModelCodeReviewRequest,
)
from roboco.models.optimal import (
    IndexDecisionParams,
    IndexErrorParams,
)
from roboco.models.permissions import KBAction
from roboco.services.optimal import (
    IndexType,
    QueryContext,
    get_optimal_service,
)
from roboco.services.optimal_brain import get_mentor_service, get_reviewer_service

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
    permissions: PermissionServiceDep,
) -> IndexResponse:
    """
    Index code files/directories.

    Indexes source code for semantic search. Supports:
    - Individual files
    - Directories
    - Glob patterns (e.g., "src/**/*.py")
    """
    if not permissions.can_perform_kb_action(agent, KBAction.INDEX_CODE):
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
    permissions: PermissionServiceDep,
) -> IndexResponse:
    """
    Index documentation files.

    Indexes markdown, text, and other documentation. Supports:
    - Local files and directories
    - URLs (single page or crawl with /**)
    - Glob patterns
    """
    if not permissions.can_perform_kb_action(agent, KBAction.INDEX_DOCS):
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
    import asyncio

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

    # Timeout after 30 seconds to prevent hanging
    search_timeout = 30.0
    try:
        async with asyncio.timeout(search_timeout):
            service = await get_optimal_service()
            results = await service.search(
                query=request.query,
                context=context,
                top_k=request.top_k,
            )
    except TimeoutError as e:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Search timed out after {search_timeout}s",
        ) from e

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
    import asyncio

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

    # RAG queries take longer due to LLM call - 60 second timeout
    rag_timeout = 60.0
    try:
        async with asyncio.timeout(rag_timeout):
            service = await get_optimal_service()
            response = await service.query(
                query=request.query,
                context=context,
                top_k=request.top_k,
            )
    except TimeoutError as e:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"RAG query timed out after {rag_timeout}s",
        ) from e
    except RuntimeError as e:
        # Service not initialized or other runtime errors
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"RAG service error: {e}",
        ) from e
    except Exception as e:
        # Catch-all for unexpected errors
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"RAG query failed: {e}",
        ) from e

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
        search_stats=response.search_stats if response.search_stats else None,
        search_errors=response.search_errors if response.search_errors else None,
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
    import asyncio

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

    # Timeout after 30 seconds
    search_timeout = 30.0
    try:
        async with asyncio.timeout(search_timeout):
            service = await get_optimal_service()
            results = await service.search(
                query=request.query,
                context=context,
                top_k=request.top_k,
            )
    except TimeoutError as e:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Context retrieval timed out after {search_timeout}s",
        ) from e

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
    permissions: PermissionServiceDep,
) -> IndexStatsResponse:
    """Get statistics about all indexes."""
    if not permissions.can_perform_kb_action(agent, KBAction.VIEW_STATS):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view index statistics",
        )

    service = await get_optimal_service()
    stats = await service.get_all_index_stats()

    return IndexStatsResponse(
        initialized=stats.get("initialized", False),
        indexes=stats.get("indexes", {}),
    )


@router.get("/stats/{index_type}", response_model=SingleIndexStatsResponse)
async def get_single_index_stats(
    index_type: str,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
) -> SingleIndexStatsResponse:
    """Get statistics for a specific index type."""
    if not permissions.can_perform_kb_action(agent, KBAction.VIEW_STATS):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view index statistics",
        )

    # Validate index type
    try:
        idx_type = IndexType(index_type)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid index type: {index_type}",
        ) from e

    service = await get_optimal_service()
    stats = await service.get_index_stats(idx_type)

    return SingleIndexStatsResponse(
        index_type=stats["index_type"],
        document_count=stats["document_count"],
        chunk_count=stats["chunk_count"],
        last_updated=stats.get("last_updated"),
    )


@router.get("/stats/staleness")
async def check_staleness(
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
) -> dict[str, Any]:
    """
    Check if indexes are stale (source files modified after last indexing).

    Returns staleness info for file-based indexes (CODE, DOCUMENTATION).
    """
    if not permissions.can_perform_kb_action(agent, KBAction.VIEW_STATS):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view index staleness",
        )

    service = await get_optimal_service()
    return await service.check_index_staleness()


@router.get("/health", response_model=RAGHealthResponse)
async def rag_health_check() -> RAGHealthResponse:
    """
    Check RAG system health.

    Tests connectivity to:
    - Embedding model (sentence-transformers)
    - LLM (Ollama for HyDE)
    - Vector store (PostgreSQL/pgvector)

    Each test has a 10-second timeout to prevent hanging.
    """
    import asyncio

    import httpx

    from roboco.config import settings

    details: dict[str, Any] = {}
    embedding_ok = False
    llm_ok = False
    vector_ok = False

    health_timeout = 10.0  # seconds

    # Test embedding model with timeout
    from roboco.services.optimal_brain.shared_embedder import (
        get_shared_embedder,
    )

    try:
        async with asyncio.timeout(health_timeout):
            embedder = await get_shared_embedder(model=settings.default_embedding_model)
            # Use async method if available (OllamaEmbedder), else run sync in thread
            if hasattr(embedder, "aembed_query"):
                test_embedding = await embedder.aembed_query("health check")
            else:
                test_embedding = await asyncio.to_thread(
                    embedder.embed_query, "health check"
                )
            if test_embedding and len(test_embedding) == settings.embedding_dimensions:
                embedding_ok = True
                details["embedding_model"] = settings.default_embedding_model
                details["embedding_dimensions"] = len(test_embedding)
    except TimeoutError:
        details["embedding_error"] = f"Timeout after {health_timeout}s"
    except Exception as e:
        details["embedding_error"] = str(e)

    # Test LLM (Ollama) - already has timeout via httpx
    try:
        async with httpx.AsyncClient(timeout=health_timeout) as client:
            resp = await client.post(
                f"{settings.local_llm_base_url}/chat/completions",
                json={
                    "model": settings.local_llm_model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "max_tokens": 5,
                },
            )
            if resp.is_success:
                llm_ok = True
                details["llm_model"] = settings.local_llm_model
                details["llm_base_url"] = settings.local_llm_base_url
    except Exception as e:
        details["llm_error"] = str(e)

    # Test vector store with timeout
    try:
        async with asyncio.timeout(health_timeout):
            service = await get_optimal_service()
            stats = await service.get_stats()
            if stats.get("initialized"):
                vector_ok = True
                details["vector_store"] = "connected"

            # Test actual search capability per index
            index_health: dict[str, str] = {}
            for index_type, plugin in service._plugins.items():
                try:
                    outcome = await plugin.search("test", top_k=1)
                    if outcome.success:
                        index_health[index_type.value] = "ok"
                    else:
                        err_msg = outcome.error_message or "unknown"
                        index_health[index_type.value] = f"error: {err_msg}"
                except Exception as idx_e:
                    index_health[index_type.value] = f"error: {idx_e}"
            details["index_health"] = index_health

    except TimeoutError:
        details["vector_store_error"] = f"Timeout after {health_timeout}s"
    except Exception as e:
        details["vector_store_error"] = str(e)

    return RAGHealthResponse(
        healthy=embedding_ok and llm_ok and vector_ok,
        embedding_status="ok" if embedding_ok else "error",
        llm_status="ok" if llm_ok else "error",
        vector_store_status="ok" if vector_ok else "error",
        details=details,
    )


@router.delete("/kb/{index_type}", response_model=ClearIndexResponse)
async def clear_index(
    index_type: str,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
) -> ClearIndexResponse:
    """
    Clear a specific index.

    Warning: This permanently deletes all documents in the index.
    """
    if not permissions.can_perform_kb_action(agent, KBAction.CLEAR_INDEX):
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


@router.get("/kb/{index_type}/documents", response_model=DocumentListResponse)
async def list_documents(
    index_type: str,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
    db: DbSession,
    pagination: PaginationDep,
) -> DocumentListResponse:
    """
    List documents in a specific index.

    Returns indexed documents with their metadata for browsing.
    """
    from sqlalchemy import func, select

    from roboco.db.tables import IndexedDocumentTable

    if not permissions.can_perform_kb_action(agent, KBAction.VIEW_STATS):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view index documents",
        )

    try:
        idx_type = IndexType(index_type)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid index type: {e}",
        ) from e

    # Query the indexed documents table
    query = (
        select(IndexedDocumentTable)
        .where(IndexedDocumentTable.index_type == idx_type.value)
        .order_by(IndexedDocumentTable.indexed_at.desc())
        .offset(pagination.offset)
        .limit(pagination.limit)
    )
    result = await db.execute(query)
    docs = result.scalars().all()

    # Get total count
    count_query = (
        select(func.count())
        .select_from(IndexedDocumentTable)
        .where(IndexedDocumentTable.index_type == idx_type.value)
    )
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    return DocumentListResponse(
        documents=[
            DocumentListItem(
                id=str(doc.id),
                source=doc.source,
                indexed_at=doc.indexed_at.isoformat() if doc.indexed_at else "",
                metadata={
                    "title": doc.title,
                    "preview": doc.preview,
                    "chunk_count": doc.chunk_count,
                    **(doc.extra_data or {}),
                },
            )
            for doc in docs
        ],
        total=total,
        index_type=index_type,
    )


@router.post("/kb/refresh", response_model=RefreshIndexResponse)
async def refresh_index(
    request: RefreshRequest,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
) -> RefreshIndexResponse:
    """
    Refresh an index with updated sources.

    Re-indexes the specified sources to pick up changes.
    """
    if not permissions.can_perform_kb_action(agent, KBAction.REFRESH_INDEX):
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


@router.post("/kb/reindex")
async def reindex_all(
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
    force: bool = False,
    timeout_seconds: int = 300,  # 5 minute default
) -> dict[str, Any]:
    """
    Trigger re-indexing of code and documentation.

    Re-scans the codebase and docs directories to update indexes.
    This is useful when files have been added/changed outside of normal
    workflow or to recover from indexing issues.

    Args:
        force: If True, reindex even if indexes aren't empty
        timeout_seconds: Maximum time to wait for reindexing (default: 300s)

    Returns:
        Detailed indexing report with success/failure counts
    """
    import asyncio

    if not permissions.can_perform_kb_action(agent, KBAction.INDEX_CODE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to trigger reindexing",
        )

    try:
        async with asyncio.timeout(timeout_seconds):
            service = await get_optimal_service()
            # Call the private method that returns the report
            report = await service._auto_index_on_startup(force=force)
            return {"status": "reindexed", **report.to_dict()}
    except TimeoutError as e:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Reindexing timed out after {timeout_seconds} seconds. "
            "Try indexing smaller directories or increasing timeout.",
        ) from e


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
    template_id = str(uuid4())
    created_at = datetime.now(UTC).isoformat()

    service = await get_optimal_service()
    template = service.create_prompt_template(
        {
            "id": template_id,
            "name": request.name,
            "template": request.template,
            "description": request.description,
            "variables": request.variables,
            "category": request.category,
            "created_at": created_at,
            "created_by": str(agent.agent_id),
        }
    )

    return PromptTemplateResponse(
        id=template["id"],
        name=template["name"],
        template=template["template"],
        description=template["description"],
        variables=template["variables"],
        category=template["category"],
        created_at=template["created_at"],
    )


@router.get("/prompts", response_model=list[PromptTemplateResponse])
async def list_prompt_templates(
    agent: CurrentAgentContext,
    category: str | None = None,
) -> list[PromptTemplateResponse]:
    """List all prompt templates, optionally filtered by category."""
    _ = agent  # Used for authentication

    service = await get_optimal_service()
    templates = service.list_prompt_templates(category=category)

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
# OPTIMAL BRAIN - MENTOR ENDPOINTS
# =============================================================================


@router.post("/mentor/ask", response_model=MentorAskResponse)
async def mentor_ask(
    request: MentorAskRequest,
    agent: CurrentAgentContext,
) -> MentorAskResponse:
    """
    Ask the organizational knowledge base for help.

    Conversational RAG - use conversation_id for follow-up questions.
    """
    # Initialize services with proper error handling
    try:
        mentor = await get_mentor_service()
        service = await get_optimal_service()

        # Initialize mentor with optimal service if needed
        if mentor._optimal_service is None:
            await mentor.initialize(service)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Mentor service initialization failed: {e}",
        ) from e

    response = await mentor.ask(
        question=request.question,
        agent_id=str(agent.agent_id),
        conversation_id=request.conversation_id,
        domain=request.domain,
    )

    return MentorAskResponse(
        answer=response.answer,
        sources=[
            SearchResultResponse(
                content=s.content,
                source=s.source,
                score=s.score,
                index_type=s.index_type.value,
                metadata=s.metadata,
            )
            for s in response.sources
        ],
        conversation_id=response.conversation_id,
        suggested_followups=response.suggested_followups,
        search_stats=response.search_stats if response.search_stats else None,
        search_errors=response.search_errors if response.search_errors else None,
    )


# =============================================================================
# OPTIMAL BRAIN - ERROR ENDPOINTS
# =============================================================================


@router.post("/errors/search", response_model=ErrorSearchResponse)
async def search_errors(
    request: ErrorSearchRequest,
    agent: CurrentAgentContext,
) -> ErrorSearchResponse:
    """
    Search for known solutions to an error.

    Before debugging from scratch, check if someone already solved this!
    """
    _ = agent  # Used for authentication
    service = await get_optimal_service()
    results = await service.search_errors(
        error_message=request.error_message,
        context=request.context,
    )

    return ErrorSearchResponse(
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
        total=len(results),
    )


@router.post("/errors/record", response_model=ErrorRecordResponse)
async def record_error(
    request: ErrorRecordRequest,
    agent: CurrentAgentContext,
) -> ErrorRecordResponse:
    """
    Record how you solved an error for future agents.
    """
    service = await get_optimal_service()
    params = IndexErrorParams(
        error_message=request.error_message,
        context=request.context,
        solution=request.solution,
        worked=request.worked,
        agent_id=agent.agent_id,
        tags=request.tags,
    )
    await service.index_error(params)

    # Generate error ID from hash
    error_hash = hashlib.md5(request.error_message.encode()).hexdigest()[:12]

    return ErrorRecordResponse(
        error_id=f"err-{error_hash}",
        status="recorded",
    )


# =============================================================================
# OPTIMAL BRAIN - DECISION ENDPOINTS
# =============================================================================


@router.post("/decisions/check", response_model=DecisionCheckResponse)
async def check_decision(
    request: DecisionCheckRequest,
    agent: CurrentAgentContext,
) -> DecisionCheckResponse:
    """
    Check if a similar decision was made before.
    """
    _ = agent  # Used for authentication
    service = await get_optimal_service()
    decisions = await service.check_decision(request.topic)

    # Convert Decision objects to dicts for response
    decision_dicts = []
    for d in decisions:
        if hasattr(d, "__dict__"):
            decision_dicts.append(
                {
                    "topic": getattr(d, "topic", ""),
                    "decision": getattr(d, "decision", ""),
                    "rationale": getattr(d, "rationale", ""),
                    "context": getattr(d, "context", ""),
                }
            )
        elif isinstance(d, dict):
            decision_dicts.append(d)

    has_precedent = len(decision_dicts) > 0
    recommendation = ""
    if has_precedent:
        recommendation = (
            f"Found {len(decision_dicts)} similar past decision(s). "
            "Review them before making a new decision."
        )

    return DecisionCheckResponse(
        has_precedent=has_precedent,
        decisions=decision_dicts,
        recommendation=recommendation,
    )


@router.post("/decisions/record", response_model=DecisionRecordResponse)
async def record_decision(
    request: DecisionRecordRequest,
    agent: CurrentAgentContext,
) -> DecisionRecordResponse:
    """
    Record an architectural or design decision.
    """
    service = await get_optimal_service()
    params = IndexDecisionParams(
        topic=request.topic,
        decision=request.decision,
        rationale=request.rationale,
        alternatives=request.alternatives,
        context=request.context,
        agent_id=agent.agent_id,
        scope=request.scope,
        tags=request.tags,
    )
    await service.index_decision(params)

    # Generate decision ID from hash
    topic_hash = hashlib.md5(request.topic.encode()).hexdigest()[:12]

    return DecisionRecordResponse(
        decision_id=f"dec-{topic_hash}",
        status="recorded",
    )


# =============================================================================
# OPTIMAL BRAIN - STANDARDS ENDPOINTS
# =============================================================================


@router.post("/standards/get", response_model=StandardsGetResponse)
async def get_standards(
    request: StandardsGetRequest,
    agent: CurrentAgentContext,
) -> StandardsGetResponse:
    """
    Get coding/security/workflow standards for a domain.
    """
    _ = agent  # Used for authentication
    service = await get_optimal_service()
    results = await service.get_standards(
        domain=request.domain,
        language=request.language,
    )

    return StandardsGetResponse(
        standards=[
            SearchResultResponse(
                content=r.content,
                source=r.source,
                score=r.score,
                index_type=r.index_type.value,
                metadata=r.metadata,
            )
            for r in results
        ],
        total=len(results),
    )


@router.post("/standards/validate", response_model=ValidateActionResponse)
async def validate_action(
    request: ValidateActionRequest,
    agent: CurrentAgentContext,
) -> ValidateActionResponse:
    """
    Validate an action against organizational standards.
    """
    _ = agent  # Used for authentication
    service = await get_optimal_service()

    # Get relevant standards for the action type
    # TODO: Use LLM to check request.context against standards
    results = await service.get_standards(
        domain=request.action_type,  # Use action_type as domain filter
    )

    # For now, return all as allowed with no violations
    # In production, this would use an LLM to check the context against standards
    return ValidateActionResponse(
        allowed=True,
        violations=[],
        warnings=[],
        relevant_standards=[
            SearchResultResponse(
                content=r.content,
                source=r.source,
                score=r.score,
                index_type=r.index_type.value,
                metadata=r.metadata,
            )
            for r in results[:5]
        ],
    )


# =============================================================================
# CODE REVIEW ENDPOINTS
# =============================================================================


@router.post("/review/code", response_model=CodeReviewResponse)
async def review_code(
    request: CodeReviewRequest,
    agent: CurrentAgentContext,
) -> CodeReviewResponse:
    """
    Review code and get feedback.

    Uses the ReviewerService to analyze code against:
    - Coding standards
    - Security policies
    - Past review comments
    - Known error patterns
    """
    _ = agent  # Used for authentication
    reviewer = await get_reviewer_service()
    service = await get_optimal_service()

    # Initialize reviewer with optimal service if needed
    if reviewer._optimal_service is None:
        await reviewer.initialize(service)

    # Build the model request
    model_request = ModelCodeReviewRequest(
        code=request.code,
        file_path=request.file_path,
        change_type=request.change_type,
    )

    result = await reviewer.review_code(model_request)

    return CodeReviewResponse(
        file_path=result.file_path,
        approved=result.approved,
        score=result.score,
        comments=result.comments,
        standards_checked=result.standards_checked,
        similar_reviews=result.similar_reviews,
    )


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


# =============================================================================
# LEARNING ENDPOINTS
# =============================================================================


@router.post("/learnings/record", response_model=LearningRecordResponse)
async def record_learning(
    request: LearningRecordRequest,
    agent: CurrentAgentContext,
) -> LearningRecordResponse:
    """
    Record a learning for cross-agent knowledge sharing.
    """
    from roboco.services.optimal_brain.indexes.learnings import (
        RecordLearningParams as LearningParams,
    )

    service = await get_optimal_service()
    params = LearningParams(
        content=request.content,
        category=request.category,
        agent_id=agent.agent_id,
        agent_role=agent.role,
        team=request.team,
        shareable=request.shareable,
        tags=request.tags,
    )
    learning_id = await service.record_learning(params)

    return LearningRecordResponse(
        learning_id=learning_id,
        status="recorded",
    )


@router.post("/learnings/search", response_model=SearchResponse)
async def search_learnings(
    request: LearningSearchRequest,
    agent: CurrentAgentContext,
) -> SearchResponse:
    """
    Search for relevant learnings.
    """
    _ = agent  # Used for authentication
    service = await get_optimal_service()
    results = await service.search_learnings(
        query=request.query,
        category=request.category,
        team=request.team,
        top_k=request.top_k,
    )

    return SearchResponse(
        query=request.query,
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
        total=len(results),
    )


# =============================================================================
# Proactive Context Endpoints
# =============================================================================


@router.post("/context/proactive", response_model=ProactiveContextResponse)
async def get_proactive_context(
    request: ProactiveContextRequest,
    agent: CurrentAgentContext,
) -> ProactiveContextResponse:
    """
    Get proactive context for a task.

    Fetches relevant knowledge to help an agent working on a task:
    - Similar past tasks and their learnings
    - Relevant code patterns
    - Applicable standards
    - Recent decisions
    - Known issues
    """
    from roboco.services.proactive import get_proactive_service

    proactive = await get_proactive_service()
    context = await proactive.get_context_for_task(
        task_id=request.task_id,
        agent_id=agent.agent_id,
    )

    # Convert to response format
    def to_items(results: list) -> list[ProactiveContextItem]:
        return [
            ProactiveContextItem(
                content=r.content,
                source=r.source,
                score=r.score,
                index_type=r.index_type.value,
                metadata=r.metadata,
            )
            for r in results
        ]

    return ProactiveContextResponse(
        task_id=context.task_id,
        agent_id=context.agent_id,
        similar_tasks=to_items(context.similar_tasks),
        relevant_learnings=to_items(context.relevant_learnings),
        code_patterns=to_items(context.code_patterns),
        applicable_standards=to_items(context.applicable_standards),
        recent_decisions=to_items(context.recent_decisions),
        known_issues=to_items(context.known_issues),
        summary=context.summary,
    )
