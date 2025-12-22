"""
Session Routes

Session management within groups. Sessions bound messages
by time, count, or content length.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from roboco.api.deps import CurrentAgentId, DbSession
from roboco.api.schemas.sessions import (
    ListSessionsParams,
    SessionCreateRequest,
    SessionForTasksCreateRequest,
    SessionListResponse,
    SessionResponse,
    SessionTaskLinkRequest,
    SessionTaskLinkResponse,
    SessionTaskLinksResponse,
)
from roboco.db.tables import (
    ChannelTable,
    GroupTable,
    SessionTable,
    SessionTaskTable,
)
from roboco.models import SessionStatus
from roboco.models.session import (
    SessionForTasksCreate,
    SessionTaskRelationshipType,
)
from roboco.services import ConflictError, NotFoundError
from roboco.services.messaging import get_messaging_service
from roboco.services.permissions import has_privileged_access, is_pm_role
from roboco.utils.converters import require_uuid

router = APIRouter()


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
    params: Annotated[ListSessionsParams, Depends()],
) -> SessionListResponse:
    """List sessions for a group."""
    # Verify group access
    group_result = await db.execute(
        select(GroupTable)
        .where(GroupTable.id == params.group_id)
        .options(selectinload(GroupTable.channel))
    )
    group = group_result.scalar_one_or_none()

    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found",
        )

    # Check channel access (privileged roles bypass membership check)
    channel = group.channel
    has_access = (
        agent_id in channel.members
        or agent_id in channel.silent_observers
        or await has_privileged_access(db, agent_id)
    )
    if not has_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this group",
        )

    # Query sessions
    query = select(SessionTable).where(SessionTable.group_id == params.group_id)

    if params.status_filter:
        query = query.where(SessionTable.status == params.status_filter)

    query = query.order_by(SessionTable.started_at.desc()).limit(params.limit)

    result = await db.execute(query)
    sessions = result.scalars().all()

    items = [
        SessionResponse(
            id=require_uuid(s.id),
            group_id=require_uuid(s.group_id),
            status=s.status,
            scope=s.scope,
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
    _agent_id: CurrentAgentId,
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
        id=require_uuid(session.id),
        group_id=require_uuid(session.group_id),
        status=session.status,
        scope=session.scope,
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

    # Check write access to channel (privileged roles bypass membership check)
    channel = group.channel
    has_write_access = agent_id in channel.writers or await has_privileged_access(
        db, agent_id
    )
    if not has_write_access:
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
        active_session.closed_at = datetime.now(UTC)

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
        id=require_uuid(session.id),
        group_id=require_uuid(session.group_id),
        status=session.status,
        scope=session.scope,
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
    _agent_id: CurrentAgentId,
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
    session.closed_at = datetime.now(UTC)

    # Clear group's active session
    group_result = await db.execute(
        select(GroupTable).where(GroupTable.id == session.group_id)
    )
    group = group_result.scalar_one_or_none()
    if group and group.active_session_id == session_id:
        group.active_session_id = None

    await db.flush()

    return SessionResponse(
        id=require_uuid(session.id),
        group_id=require_uuid(session.group_id),
        status=session.status,
        scope=session.scope,
        message_count=session.message_count,
        total_content_length=session.total_content_length,
        started_at=session.started_at,
        last_activity_at=session.last_activity_at,
        closed_at=session.closed_at,
    )


# =============================================================================
# SESSION-TASK ROUTES
# =============================================================================


def _link_to_response(link: SessionTaskTable) -> SessionTaskLinkResponse:
    """Convert SessionTaskTable to response model."""
    return SessionTaskLinkResponse(
        id=require_uuid(link.id),
        session_id=require_uuid(link.session_id),
        task_id=require_uuid(link.task_id),
        is_primary=link.is_primary,
        relationship_type=link.relationship_type,
        added_at=link.added_at,
        added_by=require_uuid(link.added_by) if link.added_by else None,
    )


@router.get(
    "/for-task/{task_id}",
    response_model=list[SessionTaskLinkResponse],
    summary="Get sessions for task",
    description="Get all sessions linked to a specific task.",
)
async def get_sessions_for_task(
    task_id: UUID,
    db: DbSession,
) -> list[SessionTaskLinkResponse]:
    """Get sessions linked to a task.

    Returns session links with session_id, is_primary, and relationship_type.
    Any agent assigned to the task can access this.
    """
    messaging = get_messaging_service(db)
    links = await messaging.get_sessions_for_task(task_id)
    return [_link_to_response(link) for link in links]


@router.post(
    "/for-tasks",
    response_model=SessionTaskLinksResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create session for tasks",
    description="Create a work session linked to one or more tasks (PM only).",
)
async def create_session_for_tasks(
    db: DbSession,
    agent_id: CurrentAgentId,
    data: SessionForTasksCreateRequest,
) -> SessionTaskLinksResponse:
    """Create a session linked to tasks (PM only)."""
    # Verify PM permission
    if not await is_pm_role(db, agent_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only PMs can create task-linked sessions",
        )

    # Verify channel exists
    channel_result = await db.execute(
        select(ChannelTable).where(ChannelTable.slug == data.channel_slug)
    )
    channel = channel_result.scalar_one_or_none()
    if not channel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Channel '{data.channel_slug}' not found",
        )

    # Create session with links using service
    messaging = get_messaging_service(db)

    try:
        rel_type = SessionTaskRelationshipType(data.relationship_type)
    except ValueError:
        rel_type = SessionTaskRelationshipType.DISCUSSION

    req = SessionForTasksCreate(
        task_ids=data.task_ids,
        channel_slug=data.channel_slug,
        relationship_type=rel_type,
    )

    try:
        session, links = await messaging.create_session_for_tasks(req, agent_id)
    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    session_response = SessionResponse(
        id=require_uuid(session.id),
        group_id=require_uuid(session.group_id),
        status=session.status,
        scope=session.scope,
        message_count=session.message_count,
        total_content_length=session.total_content_length,
        started_at=session.started_at,
        last_activity_at=session.last_activity_at,
        closed_at=session.closed_at,
    )

    return SessionTaskLinksResponse(
        session=session_response,
        links=[_link_to_response(link) for link in links],
    )


@router.post(
    "/{session_id}/tasks",
    response_model=SessionTaskLinkResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Link task to session",
    description="Link a task to an existing session (PM only).",
)
async def link_task_to_session(
    db: DbSession,
    agent_id: CurrentAgentId,
    session_id: UUID,
    data: SessionTaskLinkRequest,
) -> SessionTaskLinkResponse:
    """Link a task to a session (PM only)."""
    if not await is_pm_role(db, agent_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only PMs can link tasks to sessions",
        )

    messaging = get_messaging_service(db)

    try:
        rel_type = SessionTaskRelationshipType(data.relationship_type)
    except ValueError:
        rel_type = SessionTaskRelationshipType.DISCUSSION

    try:
        link = await messaging.link_session_to_task(
            session_id=session_id,
            task_id=data.task_id,
            added_by=agent_id,
            is_primary=data.is_primary,
            relationship_type=rel_type,
        )
    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    except ConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        ) from e

    return _link_to_response(link)


@router.delete(
    "/{session_id}/tasks/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Unlink task from session",
    description="Remove a task from a session (PM only).",
)
async def unlink_task_from_session(
    db: DbSession,
    agent_id: CurrentAgentId,
    session_id: UUID,
    task_id: UUID,
) -> None:
    """Unlink a task from a session (PM only)."""
    if not await is_pm_role(db, agent_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only PMs can unlink tasks from sessions",
        )

    messaging = get_messaging_service(db)
    removed = await messaging.unlink_session_from_task(session_id, task_id)

    if not removed:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session-task link not found",
        )


@router.get(
    "/{session_id}/tasks",
    response_model=list[SessionTaskLinkResponse],
    summary="Get tasks for session",
    description="Get all tasks linked to a session.",
)
async def get_tasks_for_session(
    db: DbSession,
    _agent_id: CurrentAgentId,
    session_id: UUID,
) -> list[SessionTaskLinkResponse]:
    """Get all tasks linked to a session."""
    # Verify session exists
    session_result = await db.execute(
        select(SessionTable).where(SessionTable.id == session_id)
    )
    if not session_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    messaging = get_messaging_service(db)
    links = await messaging.get_tasks_for_session(session_id)

    return [_link_to_response(link) for link in links]
