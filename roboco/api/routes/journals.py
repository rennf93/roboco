"""
Journal API Routes

Agent personal journals for reflection, growth tracking, and debugging.

"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from roboco.api.deps import CurrentAgentContext, DbSession
from roboco.api.schemas.journals import (
    CreateEntryRequest,
    DecisionLogRequest,
    GeneralEntryRequest,
    GrowthMetricsResponse,
    JournalEntryResponse,
    JournalResponse,
    JournalStatsResponse,
    LearningRequest,
    ListEntriesParams,
    SearchEntriesRequest,
    StruggleRequest,
    TaskReflectionRequest,
)
from roboco.enforcement import JournalAccessDeniedError, validate_journal_access
from roboco.models.base import JournalEntryType
from roboco.models.journal import (
    DecisionLogParams,
    GeneralEntryParams,
    JournalEntryCreate,
    LearningEntryParams,
    StruggleEntryParams,
    TaskReflectionParams,
)
from roboco.services.journal import ListEntriesFilter, get_journal_service

router = APIRouter()


# =============================================================================
# /me ENDPOINTS - MUST BE DEFINED BEFORE /{agent_id} ENDPOINTS
# =============================================================================


@router.get("/me", response_model=JournalResponse)
async def get_my_journal(
    agent: CurrentAgentContext,
    db: DbSession,
) -> JournalResponse:
    """Get or create the current agent's journal."""
    service = get_journal_service(db)
    journal = await service.get_or_create_journal(agent.agent_id)

    return JournalResponse(
        id=journal.id,
        agent_id=journal.agent_id,
        total_entries=journal.total_entries,
        last_entry_at=journal.last_entry_at,
        latest_summary=journal.latest_summary,
        summary_updated_at=journal.summary_updated_at,
        entries_by_type=journal.entries_by_type,
        created_at=journal.created_at,
        updated_at=journal.updated_at,
    )


@router.get("/me/entries", response_model=list[JournalEntryResponse])
async def list_my_entries(
    agent: CurrentAgentContext,
    db: DbSession,
    params: Annotated[ListEntriesParams, Depends()],
) -> list[JournalEntryResponse]:
    """List the current agent's journal entries."""
    service = get_journal_service(db)
    journal = await service.get_journal_by_agent(agent.agent_id)

    if not journal:
        return []

    type_filter = None
    if params.entry_type:
        try:
            type_filter = JournalEntryType(params.entry_type)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid entry type: {e}",
            ) from e

    entries = await service.list_entries(
        journal_id=journal.id,
        filters=ListEntriesFilter(
            entry_type=type_filter,
            task_id=params.task_id,
            limit=params.limit,
            offset=params.offset,
            include_private=True,  # Can see own private entries
        ),
    )

    return [
        JournalEntryResponse(
            id=e.id,
            journal_id=e.journal_id,
            type=e.type.value if isinstance(e.type, JournalEntryType) else e.type,
            title=e.title,
            content=e.content,
            task_id=e.task_id,
            session_id=e.session_id,
            timestamp=e.timestamp,
            tags=e.tags,
            sentiment=e.sentiment,
            is_private=e.is_private,
            created_at=e.created_at,
            updated_at=e.updated_at,
        )
        for e in entries
    ]


@router.post(
    "/me/entries",
    response_model=JournalEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_entry(
    request: CreateEntryRequest,
    agent: CurrentAgentContext,
    db: DbSession,
) -> JournalEntryResponse:
    """Create a new journal entry."""
    service = get_journal_service(db)
    journal = await service.get_or_create_journal(agent.agent_id)

    try:
        entry_type = JournalEntryType(request.type)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid entry type: {e}",
        ) from e

    entry_create = JournalEntryCreate(
        journal_id=journal.id,
        type=entry_type,
        title=request.title,
        content=request.content,
        task_id=request.task_id,
        session_id=request.session_id,
        tags=request.tags,
        sentiment=request.sentiment,
        is_private=request.is_private,
    )

    entry = await service.create_entry(entry_create)

    return JournalEntryResponse(
        id=entry.id,
        journal_id=entry.journal_id,
        type=entry.type.value
        if isinstance(entry.type, JournalEntryType)
        else entry.type,
        title=entry.title,
        content=entry.content,
        task_id=entry.task_id,
        session_id=entry.session_id,
        timestamp=entry.timestamp,
        tags=entry.tags,
        sentiment=entry.sentiment,
        is_private=entry.is_private,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


@router.get("/me/stats", response_model=JournalStatsResponse)
async def get_my_stats(
    agent: CurrentAgentContext,
    db: DbSession,
) -> JournalStatsResponse:
    """Get statistics for the current agent's journal."""
    service = get_journal_service(db)
    journal = await service.get_journal_by_agent(agent.agent_id)

    if not journal:
        return JournalStatsResponse(
            total_entries=0,
            entries_by_type={},
            last_entry_at=None,
            has_summary=False,
        )

    stats = await service.get_journal_stats(journal.id)
    if not stats:
        return JournalStatsResponse(
            total_entries=0,
            entries_by_type={},
            last_entry_at=None,
            has_summary=False,
        )

    return JournalStatsResponse(
        total_entries=stats.total_entries,
        entries_by_type=stats.entries_by_type,
        last_entry_at=stats.last_entry_at,
        has_summary=stats.has_summary,
    )


@router.get("/me/growth", response_model=GrowthMetricsResponse)
async def get_my_growth_metrics(
    agent: CurrentAgentContext,
    db: DbSession,
) -> GrowthMetricsResponse:
    """Get growth metrics for the current agent."""
    service = get_journal_service(db)
    metrics = await service.get_growth_metrics(agent.agent_id)

    if not metrics:
        return GrowthMetricsResponse(
            total_reflections=0,
            total_learnings=0,
            total_struggles=0,
            total_decisions=0,
            struggle_resolution_rate=0.0,
            learning_frequency=0.0,
            sentiment_trend="stable",
        )

    return GrowthMetricsResponse(
        total_reflections=metrics.total_reflections,
        total_learnings=metrics.total_learnings,
        total_struggles=metrics.total_struggles,
        total_decisions=metrics.total_decisions,
        struggle_resolution_rate=metrics.struggle_resolution_rate,
        learning_frequency=metrics.learning_frequency,
        sentiment_trend=metrics.sentiment_trend,
    )


@router.post("/me/search", response_model=list[JournalEntryResponse])
async def search_my_entries(
    request: SearchEntriesRequest,
    agent: CurrentAgentContext,
    db: DbSession,
) -> list[JournalEntryResponse]:
    """
    Semantic search through the current agent's journal.

    Uses RAG to find relevant entries.
    """
    service = get_journal_service(db)
    entries = await service.search_entries(
        agent_id=agent.agent_id,
        query=request.query,
        top_k=request.top_k,
    )

    return [
        JournalEntryResponse(
            id=e.id,
            journal_id=e.journal_id,
            type=e.type.value if isinstance(e.type, JournalEntryType) else e.type,
            title=e.title,
            content=e.content,
            task_id=e.task_id,
            session_id=e.session_id,
            timestamp=e.timestamp,
            tags=e.tags,
            sentiment=e.sentiment,
            is_private=e.is_private,
            created_at=e.created_at,
            updated_at=e.updated_at,
        )
        for e in entries
    ]


@router.post(
    "/me/reflections",
    response_model=JournalEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_task_reflection(
    request: TaskReflectionRequest,
    agent: CurrentAgentContext,
    db: DbSession,
) -> JournalEntryResponse:
    """Add a task reflection entry."""
    service = get_journal_service(db)
    entry = await service.add_task_reflection(
        agent_id=agent.agent_id,
        params=TaskReflectionParams(
            task_id=request.task_id,
            title=request.title,
            what_done=request.what_done,
            what_learned=request.what_learned,
            what_struggled=request.what_struggled,
            next_steps=request.next_steps,
            tags=request.tags,
        ),
    )

    return JournalEntryResponse(
        id=entry.id,
        journal_id=entry.journal_id,
        type=entry.type.value
        if isinstance(entry.type, JournalEntryType)
        else entry.type,
        title=entry.title,
        content=entry.content,
        task_id=entry.task_id,
        session_id=entry.session_id,
        timestamp=entry.timestamp,
        tags=entry.tags,
        sentiment=entry.sentiment,
        is_private=entry.is_private,
        created_at=entry.created_at,
    )


@router.post(
    "/me/decisions",
    response_model=JournalEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_decision_log(
    request: DecisionLogRequest,
    agent: CurrentAgentContext,
    db: DbSession,
) -> JournalEntryResponse:
    """Add a decision log entry."""
    service = get_journal_service(db)
    entry = await service.add_decision_log(
        agent_id=agent.agent_id,
        params=DecisionLogParams(
            title=request.title,
            context=request.context,
            options=request.options,
            chosen=request.chosen,
            rationale=request.rationale,
            consequences=request.consequences,
            task_id=request.task_id,
            tags=request.tags,
        ),
    )

    return JournalEntryResponse(
        id=entry.id,
        journal_id=entry.journal_id,
        type=entry.type.value
        if isinstance(entry.type, JournalEntryType)
        else entry.type,
        title=entry.title,
        content=entry.content,
        task_id=entry.task_id,
        session_id=entry.session_id,
        timestamp=entry.timestamp,
        tags=entry.tags,
        sentiment=entry.sentiment,
        is_private=entry.is_private,
        created_at=entry.created_at,
    )


@router.post(
    "/me/learnings",
    response_model=JournalEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_learning(
    request: LearningRequest,
    agent: CurrentAgentContext,
    db: DbSession,
) -> JournalEntryResponse:
    """Add a learning entry."""
    service = get_journal_service(db)
    entry = await service.add_learning(
        agent_id=agent.agent_id,
        params=LearningEntryParams(
            title=request.title,
            what_learned=request.what_learned,
            how_applied=request.how_applied,
            source=request.source,
            task_id=request.task_id,
            tags=request.tags,
        ),
    )

    return JournalEntryResponse(
        id=entry.id,
        journal_id=entry.journal_id,
        type=entry.type.value
        if isinstance(entry.type, JournalEntryType)
        else entry.type,
        title=entry.title,
        content=entry.content,
        task_id=entry.task_id,
        session_id=entry.session_id,
        timestamp=entry.timestamp,
        tags=entry.tags,
        sentiment=entry.sentiment,
        is_private=entry.is_private,
        created_at=entry.created_at,
    )


@router.post(
    "/me/struggles",
    response_model=JournalEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_struggle(
    request: StruggleRequest,
    agent: CurrentAgentContext,
    db: DbSession,
) -> JournalEntryResponse:
    """Add a struggle entry."""
    service = get_journal_service(db)
    entry = await service.add_struggle(
        agent_id=agent.agent_id,
        params=StruggleEntryParams(
            title=request.title,
            what_struggled=request.what_struggled,
            attempted_solutions=request.attempted_solutions,
            resolution=request.resolution,
            help_needed=request.help_needed,
            task_id=request.task_id,
            tags=request.tags,
        ),
    )

    return JournalEntryResponse(
        id=entry.id,
        journal_id=entry.journal_id,
        type=entry.type.value
        if isinstance(entry.type, JournalEntryType)
        else entry.type,
        title=entry.title,
        content=entry.content,
        task_id=entry.task_id,
        session_id=entry.session_id,
        timestamp=entry.timestamp,
        tags=entry.tags,
        sentiment=entry.sentiment,
        is_private=entry.is_private,
        created_at=entry.created_at,
    )


@router.post(
    "/me/notes",
    response_model=JournalEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_general_entry(
    request: GeneralEntryRequest,
    agent: CurrentAgentContext,
    db: DbSession,
) -> JournalEntryResponse:
    """Add a general journal entry."""
    service = get_journal_service(db)
    entry = await service.add_general_entry(
        agent_id=agent.agent_id,
        params=GeneralEntryParams(
            title=request.title,
            content=request.content,
            task_id=request.task_id,
            session_id=request.session_id,
            tags=request.tags,
            is_private=request.is_private,
        ),
    )

    return JournalEntryResponse(
        id=entry.id,
        journal_id=entry.journal_id,
        type=entry.type.value
        if isinstance(entry.type, JournalEntryType)
        else entry.type,
        title=entry.title,
        content=entry.content,
        task_id=entry.task_id,
        session_id=entry.session_id,
        timestamp=entry.timestamp,
        tags=entry.tags,
        sentiment=entry.sentiment,
        is_private=entry.is_private,
        created_at=entry.created_at,
    )


# =============================================================================
# ENTRY CRUD ENDPOINTS (non-/me paths)
# =============================================================================


@router.get("/entries/{entry_id}", response_model=JournalEntryResponse)
async def get_entry(
    entry_id: UUID,
    agent: CurrentAgentContext,
    db: DbSession,
) -> JournalEntryResponse:
    """Get a specific journal entry."""
    service = get_journal_service(db)
    entry = await service.get_entry(entry_id)

    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entry not found: {entry_id}",
        )

    # Get journal to check ownership
    journal = await service.get_journal(entry.journal_id)
    if not journal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Journal not found for entry: {entry_id}",
        )

    # Get owner's slug for permission checking
    owner_slug = await service.get_agent_slug(journal.agent_id)
    if not owner_slug:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Journal owner not found",
        )

    # Check permission (cell members can see all entries including private)
    try:
        validate_journal_access(agent.slug or "", owner_slug)
    except JournalAccessDeniedError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=e.message,
        ) from e

    return JournalEntryResponse(
        id=entry.id,
        journal_id=entry.journal_id,
        type=entry.type.value
        if isinstance(entry.type, JournalEntryType)
        else entry.type,
        title=entry.title,
        content=entry.content,
        task_id=entry.task_id,
        session_id=entry.session_id,
        timestamp=entry.timestamp,
        tags=entry.tags,
        sentiment=entry.sentiment,
        is_private=entry.is_private,
        created_at=entry.created_at,
        updated_at=entry.updated_at,
    )


@router.delete("/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_entry(
    entry_id: UUID,
    agent: CurrentAgentContext,
    db: DbSession,
) -> None:
    """Delete a journal entry."""
    service = get_journal_service(db)
    entry = await service.get_entry(entry_id)

    if not entry:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entry not found: {entry_id}",
        )

    # Check ownership
    journal = await service.get_journal(entry.journal_id)
    if journal and journal.agent_id != agent.agent_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Can only delete your own entries",
        )

    await service.delete_entry(entry_id)


# =============================================================================
# /{agent_id} ENDPOINTS - MUST BE DEFINED AFTER /me ENDPOINTS
# =============================================================================


@router.get("/{agent_id}", response_model=JournalResponse)
async def get_journal_by_agent(
    agent_id: str,
    agent: CurrentAgentContext,
    db: DbSession,
) -> JournalResponse:
    """
    Get a journal by agent ID (UUID) or slug (e.g., "be-dev-1").

    Access is restricted based on cell membership and role hierarchy.
    """
    service = get_journal_service(db)

    # Resolve agent identifier (UUID or slug) to UUID
    resolved_id = await service.resolve_agent_id(agent_id)
    if not resolved_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id}",
        )

    # Get target agent's slug for permission checking
    target_slug = await service.get_agent_slug(resolved_id)
    if not target_slug:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id}",
        )

    # Check permission
    try:
        validate_journal_access(agent.slug or "", target_slug)
    except JournalAccessDeniedError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=e.message,
        ) from e

    journal = await service.get_journal_by_agent(resolved_id)
    if not journal:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Journal not found for agent: {agent_id}",
        )

    return JournalResponse(
        id=journal.id,
        agent_id=journal.agent_id,
        total_entries=journal.total_entries,
        last_entry_at=journal.last_entry_at,
        latest_summary=journal.latest_summary,
        summary_updated_at=journal.summary_updated_at,
        entries_by_type=journal.entries_by_type,
        created_at=journal.created_at,
        updated_at=journal.updated_at,
    )


@router.get("/{agent_id}/entries", response_model=list[JournalEntryResponse])
async def list_agent_entries(
    agent_id: str,
    agent: CurrentAgentContext,
    db: DbSession,
    params: Annotated[ListEntriesParams, Depends()],
) -> list[JournalEntryResponse]:
    """
    List journal entries for a specific agent by UUID or slug.

    Access is restricted based on cell membership and role hierarchy.
    Cell members can see all entries (including private) from each other.
    """
    service = get_journal_service(db)

    # Resolve agent identifier (UUID or slug) to UUID
    resolved_id = await service.resolve_agent_id(agent_id)
    if not resolved_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id}",
        )

    # Get target agent's slug for permission checking
    target_slug = await service.get_agent_slug(resolved_id)
    if not target_slug:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id}",
        )

    # Check permission
    try:
        validate_journal_access(agent.slug or "", target_slug)
    except JournalAccessDeniedError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=e.message,
        ) from e

    journal = await service.get_journal_by_agent(resolved_id)
    if not journal:
        return []

    type_filter = None
    if params.entry_type:
        try:
            type_filter = JournalEntryType(params.entry_type)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid entry type: {e}",
            ) from e

    # Cell members with access can see all entries including private
    entries = await service.list_entries(
        journal_id=journal.id,
        filters=ListEntriesFilter(
            entry_type=type_filter,
            task_id=params.task_id,
            limit=params.limit,
            offset=params.offset,
            include_private=True,  # Full access for authorized readers
        ),
    )

    return [
        JournalEntryResponse(
            id=e.id,
            journal_id=e.journal_id,
            type=e.type.value if isinstance(e.type, JournalEntryType) else e.type,
            title=e.title,
            content=e.content,
            task_id=e.task_id,
            session_id=e.session_id,
            timestamp=e.timestamp,
            tags=e.tags,
            sentiment=e.sentiment,
            is_private=e.is_private,
            created_at=e.created_at,
            updated_at=e.updated_at,
        )
        for e in entries
    ]
