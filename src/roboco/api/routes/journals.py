"""
Journal API Routes

Agent personal journals for reflection, growth tracking, and debugging.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from roboco.api.deps import CurrentAgentContext, DbSession
from roboco.models.base import JournalEntryType
from roboco.models.journal import JournalEntryCreate
from roboco.services.journal import get_journal_service

router = APIRouter(prefix="/journals", tags=["journals"])


# =============================================================================
# REQUEST/RESPONSE SCHEMAS
# =============================================================================


class JournalResponse(BaseModel):
    """Journal response."""

    id: UUID
    agent_id: UUID
    total_entries: int
    last_entry_at: datetime | None
    latest_summary: str | None
    summary_updated_at: datetime | None
    entries_by_type: dict[str, int]
    created_at: datetime
    updated_at: datetime | None = None


class JournalEntryResponse(BaseModel):
    """Journal entry response."""

    id: UUID
    journal_id: UUID
    type: str
    title: str
    content: str
    task_id: UUID | None
    session_id: UUID | None
    timestamp: datetime
    tags: list[str]
    sentiment: str | None
    is_private: bool
    created_at: datetime
    updated_at: datetime | None = None


class CreateEntryRequest(BaseModel):
    """Request to create a journal entry."""

    type: str = Field(
        ...,
        description="Entry type (task_reflection, decision_log, learning, struggle, general)",
    )
    title: str = Field(..., min_length=1, max_length=200)
    content: str = Field(..., min_length=1)
    task_id: UUID | None = None
    session_id: UUID | None = None
    tags: list[str] = Field(default_factory=list)
    sentiment: str | None = None
    is_private: bool = False


class TaskReflectionRequest(BaseModel):
    """Request to create a task reflection entry."""

    task_id: UUID
    title: str = Field(..., min_length=1, max_length=200)
    what_done: str
    what_learned: str
    what_struggled: str
    next_steps: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class DecisionLogRequest(BaseModel):
    """Request to create a decision log entry."""

    title: str = Field(..., min_length=1, max_length=200)
    context: str
    options: list[dict[str, str]] = Field(..., min_length=2)
    chosen: str
    rationale: str
    consequences: list[str] = Field(default_factory=list)
    task_id: UUID | None = None
    tags: list[str] = Field(default_factory=list)


class LearningRequest(BaseModel):
    """Request to create a learning entry."""

    title: str = Field(..., min_length=1, max_length=200)
    what_learned: str
    how_applied: str | None = None
    source: str | None = None
    task_id: UUID | None = None
    tags: list[str] = Field(default_factory=list)


class StruggleRequest(BaseModel):
    """Request to create a struggle entry."""

    title: str = Field(..., min_length=1, max_length=200)
    what_struggled: str
    attempted_solutions: list[str] = Field(default_factory=list)
    resolution: str | None = None
    help_needed: str | None = None
    task_id: UUID | None = None
    tags: list[str] = Field(default_factory=list)


class GeneralEntryRequest(BaseModel):
    """Request to create a general journal entry."""

    title: str = Field(..., min_length=1, max_length=200)
    content: str
    task_id: UUID | None = None
    session_id: UUID | None = None
    tags: list[str] = Field(default_factory=list)
    is_private: bool = False


class JournalStatsResponse(BaseModel):
    """Journal statistics response."""

    total_entries: int
    entries_by_type: dict[str, int]
    last_entry_at: datetime | None
    has_summary: bool


class GrowthMetricsResponse(BaseModel):
    """Growth metrics response."""

    total_reflections: int
    total_learnings: int
    total_struggles: int
    total_decisions: int
    struggle_resolution_rate: float
    learning_frequency: float
    sentiment_trend: str


class SearchEntriesRequest(BaseModel):
    """Request to search journal entries."""

    query: str = Field(..., min_length=1)
    top_k: int = Field(5, ge=1, le=20)


# =============================================================================
# JOURNAL ENDPOINTS
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


@router.get("/{agent_id}", response_model=JournalResponse)
async def get_journal_by_agent(
    agent_id: UUID,
    agent: CurrentAgentContext,
    db: DbSession,
) -> JournalResponse:
    """
    Get a journal by agent ID.

    Note: Access may be restricted based on privacy settings.
    """
    service = get_journal_service(db)
    journal = await service.get_journal_by_agent(agent_id)

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


# =============================================================================
# ENTRY CRUD ENDPOINTS
# =============================================================================


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


@router.get("/me/entries", response_model=list[JournalEntryResponse])
async def list_my_entries(
    agent: CurrentAgentContext,
    db: DbSession,
    entry_type: str | None = Query(None, description="Filter by entry type"),
    task_id: UUID | None = Query(None, description="Filter by task"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> list[JournalEntryResponse]:
    """List the current agent's journal entries."""
    service = get_journal_service(db)
    journal = await service.get_journal_by_agent(agent.agent_id)

    if not journal:
        return []

    type_filter = None
    if entry_type:
        try:
            type_filter = JournalEntryType(entry_type)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid entry type: {e}",
            ) from e

    entries = await service.list_entries(
        journal_id=journal.id,
        entry_type=type_filter,
        task_id=task_id,
        limit=limit,
        offset=offset,
        include_private=True,  # Can see own private entries
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

    # Check privacy (simplified - in production would check journal ownership)
    if entry.is_private:
        journal = await service.get_journal(entry.journal_id)
        if journal and journal.agent_id != agent.agent_id:
            # Allow CEO and Auditor to see private entries
            from roboco.models.base import AgentRole

            if agent.role not in [AgentRole.CEO, AgentRole.AUDITOR]:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="This entry is private",
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
# CONVENIENCE ENTRY ENDPOINTS
# =============================================================================


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
        task_id=request.task_id,
        title=request.title,
        what_done=request.what_done,
        what_learned=request.what_learned,
        what_struggled=request.what_struggled,
        next_steps=request.next_steps,
        tags=request.tags,
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
        title=request.title,
        context=request.context,
        options=request.options,
        chosen=request.chosen,
        rationale=request.rationale,
        consequences=request.consequences,
        task_id=request.task_id,
        tags=request.tags,
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
        title=request.title,
        what_learned=request.what_learned,
        how_applied=request.how_applied,
        source=request.source,
        task_id=request.task_id,
        tags=request.tags,
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
        title=request.title,
        what_struggled=request.what_struggled,
        attempted_solutions=request.attempted_solutions,
        resolution=request.resolution,
        help_needed=request.help_needed,
        task_id=request.task_id,
        tags=request.tags,
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
        title=request.title,
        content=request.content,
        task_id=request.task_id,
        session_id=request.session_id,
        tags=request.tags,
        is_private=request.is_private,
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
# ANALYTICS ENDPOINTS
# =============================================================================


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


# =============================================================================
# SEARCH ENDPOINTS
# =============================================================================


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
