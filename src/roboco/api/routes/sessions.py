"""
Session Routes

Session management within groups. Sessions bound messages
by time, count, or content length.
"""

from datetime import datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from roboco.api.deps import CurrentAgentId, DbSession
from roboco.db.tables import ChannelTable, GroupTable, SessionTable
from roboco.models import SessionConfig, SessionCreate, SessionStatus

router = APIRouter()


# =============================================================================
# Response Models
# =============================================================================


class SessionResponse(BaseModel):
    """Session response."""

    id: UUID
    group_id: UUID
    status: SessionStatus
    message_count: int
    total_content_length: int
    started_at: datetime
    last_activity_at: datetime
    closed_at: datetime | None


class SessionListResponse(BaseModel):
    """List of sessions."""

    items: list[SessionResponse]
    total: int


class SessionCreateRequest(BaseModel):
    """Request to create a session."""

    group_id: UUID
    max_time_window_minutes: int | None = 30
    max_message_count: int | None = 100
    max_content_length: int | None = 50000
    timeout_seconds: int = 300


# =============================================================================
# Routes
# =============================================================================


@router.get(
    "",
    response_model=SessionListResponse,
    summary="List sessions",
    description="List sessions for a group.",
)
async def list_sessions(
    db: DbSession,
    agent_id: CurrentAgentId,
    group_id: UUID = Query(...),
    status_filter: SessionStatus | None = None,
    limit: int = Query(20, ge=1, le=100),
) -> SessionListResponse:
    """List sessions for a group."""
    # Verify group access
    group_result = await db.execute(
        select(GroupTable)
        .where(GroupTable.id == group_id)
        .options(selectinload(GroupTable.channel))
    )
    group = group_result.scalar_one_or_none()

    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found",
        )

    # Check channel access
    channel = group.channel
    if agent_id not in channel.members and agent_id not in channel.silent_observers:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this group",
        )

    # Query sessions
    query = select(SessionTable).where(SessionTable.group_id == group_id)

    if status_filter:
        query = query.where(SessionTable.status == status_filter)

    query = query.order_by(SessionTable.started_at.desc()).limit(limit)

    result = await db.execute(query)
    sessions = result.scalars().all()

    items = [
        SessionResponse(
            id=s.id,
            group_id=s.group_id,
            status=s.status,
            message_count=s.message_count,
            total_content_length=s.total_content_length,
            started_at=s.started_at,
            last_activity_at=s.last_activity_at,
            closed_at=s.closed_at,
        )
        for s in sessions
    ]

    return SessionListResponse(
        items=items,
        total=len(items),
    )


@router.get(
    "/{session_id}",
    response_model=SessionResponse,
    summary="Get session",
    description="Get session details.",
)
async def get_session(
    db: DbSession,
    agent_id: CurrentAgentId,
    session_id: UUID,
) -> SessionResponse:
    """Get session details."""
    query = (
        select(SessionTable)
        .where(SessionTable.id == session_id)
        .options(selectinload(SessionTable.group))
    )

    result = await db.execute(query)
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    return SessionResponse(
        id=session.id,
        group_id=session.group_id,
        status=session.status,
        message_count=session.message_count,
        total_content_length=session.total_content_length,
        started_at=session.started_at,
        last_activity_at=session.last_activity_at,
        closed_at=session.closed_at,
    )


@router.post(
    "",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create session",
    description="Create a new session in a group.",
)
async def create_session(
    db: DbSession,
    agent_id: CurrentAgentId,
    data: SessionCreateRequest,
) -> SessionResponse:
    """Create a new session."""
    # Verify group exists and agent has access
    group_result = await db.execute(
        select(GroupTable)
        .where(GroupTable.id == data.group_id)
        .options(selectinload(GroupTable.channel))
    )
    group = group_result.scalar_one_or_none()

    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found",
        )

    # Check write access to channel
    channel = group.channel
    if agent_id not in channel.writers:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have write access to this group",
        )

    # Close any existing active session
    active_result = await db.execute(
        select(SessionTable).where(
            SessionTable.group_id == data.group_id,
            SessionTable.status == SessionStatus.ACTIVE,
        )
    )
    active_session = active_result.scalar_one_or_none()

    if active_session:
        active_session.status = SessionStatus.CLOSED
        active_session.closed_at = datetime.utcnow()

    # Create new session
    session = SessionTable(
        group_id=data.group_id,
        max_time_window=(
            timedelta(minutes=data.max_time_window_minutes)
            if data.max_time_window_minutes
            else None
        ),
        max_message_count=data.max_message_count,
        max_content_length=data.max_content_length,
        timeout_seconds=data.timeout_seconds,
        status=SessionStatus.ACTIVE,
    )

    db.add(session)

    # Update group's active session
    group.active_session_id = session.id
    group.total_sessions += 1

    await db.flush()

    return SessionResponse(
        id=session.id,
        group_id=session.group_id,
        status=session.status,
        message_count=session.message_count,
        total_content_length=session.total_content_length,
        started_at=session.started_at,
        last_activity_at=session.last_activity_at,
        closed_at=session.closed_at,
    )


@router.post(
    "/{session_id}/close",
    response_model=SessionResponse,
    summary="Close session",
    description="Manually close a session.",
)
async def close_session(
    db: DbSession,
    agent_id: CurrentAgentId,
    session_id: UUID,
) -> SessionResponse:
    """Close a session."""
    result = await db.execute(select(SessionTable).where(SessionTable.id == session_id))
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    if session.status != SessionStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session is not active",
        )

    session.status = SessionStatus.CLOSED
    session.closed_at = datetime.utcnow()

    # Clear group's active session
    group_result = await db.execute(
        select(GroupTable).where(GroupTable.id == session.group_id)
    )
    group = group_result.scalar_one_or_none()
    if group and group.active_session_id == session_id:
        group.active_session_id = None

    await db.flush()

    return SessionResponse(
        id=session.id,
        group_id=session.group_id,
        status=session.status,
        message_count=session.message_count,
        total_content_length=session.total_content_length,
        started_at=session.started_at,
        last_activity_at=session.last_activity_at,
        closed_at=session.closed_at,
    )
