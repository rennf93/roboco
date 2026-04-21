"""
Session API Routes

Sessions bound messages within a group. Thin HTTP plumbing — all state
reads/writes live in `MessagingService`.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from roboco.api.deps import CurrentAgentId, DbSession
from roboco.api.schemas.sessions import (
    ListSessionsParams,
    SessionCreateRequest,
    SessionForTasksCreateRequest,
    SessionListResponse,
    SessionResponse,
    SessionTaskInfo,
    SessionTaskLinkRequest,
    SessionTaskLinkResponse,
    SessionTaskLinksResponse,
)
from roboco.db.tables import SessionTaskTable
from roboco.models.session import (
    SessionForTasksCreate,
    SessionTaskRelationshipType,
)
from roboco.services import ConflictError, NotFoundError
from roboco.services.messaging import (
    ApiSessionCreate,
    get_messaging_service,
)
from roboco.services.permissions import is_pm_role
from roboco.services.proactive import get_proactive_service
from roboco.utils.converters import require_uuid

router = APIRouter()

logger = __import__("structlog").get_logger(__name__)


async def _inject_session_context(session_id: UUID, agent_id: UUID) -> None:
    """Fire-and-forget proactive-context injection after session start.

    Failure is swallowed — we never let context-injection errors take down
    a successful session creation.
    """
    try:
        proactive = await get_proactive_service()
        context = await proactive.get_context_for_session(
            session_id=session_id, agent_id=agent_id
        )
        if context and not context.is_empty():
            logger.info(
                "Injected session proactive context",
                session_id=str(session_id),
                agent_id=str(agent_id),
            )
    except Exception as e:
        logger.warning(
            "Failed to inject session context",
            session_id=str(session_id),
            error=str(e),
        )


def _session_to_response(session) -> SessionResponse:  # type: ignore[no-untyped-def]
    """Minimal SessionTable → SessionResponse mapper."""
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


def _link_to_response(link: SessionTaskTable) -> SessionTaskLinkResponse:
    return SessionTaskLinkResponse(
        id=require_uuid(link.id),
        session_id=require_uuid(link.session_id),
        task_id=require_uuid(link.task_id),
        is_primary=link.is_primary,
        relationship_type=link.relationship_type,
        added_at=link.added_at,
        added_by=require_uuid(link.added_by) if link.added_by else None,
    )


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
    """List sessions for a group the agent can access."""
    messaging = get_messaging_service(db)
    try:
        sessions = await messaging.list_group_sessions_for_agent(
            group_id=params.group_id,
            agent_id=agent_id,
            status_filter=params.status_filter,
            limit=params.limit,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e

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
            task_links=[
                SessionTaskInfo(
                    task_id=require_uuid(link.task_id),
                    task_title=link.task.title if link.task else None,
                    is_primary=link.is_primary,
                    relationship_type=link.relationship_type,
                )
                for link in s.task_links
            ],
        )
        for s in sessions
    ]
    return SessionListResponse(items=items, total=len(items))


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
    messaging = get_messaging_service(db)
    try:
        session_row = await messaging.get_session_or_raise(session_id)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return _session_to_response(session_row)


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
    messaging = get_messaging_service(db)
    try:
        session_row = await messaging.create_session_with_access_check(
            agent_id=agent_id,
            request=ApiSessionCreate(
                group_id=data.group_id,
                max_time_window_minutes=data.max_time_window_minutes,
                max_message_count=data.max_message_count,
                max_content_length=data.max_content_length,
                timeout_seconds=data.timeout_seconds,
            ),
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e

    await _inject_session_context(require_uuid(session_row.id), agent_id)
    return _session_to_response(session_row)


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
    messaging = get_messaging_service(db)
    try:
        session_row = await messaging.close_session_or_raise(session_id)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    return _session_to_response(session_row)


# =============================================================================
# SESSION-TASK ROUTES
# =============================================================================


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
    if not await is_pm_role(db, agent_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only PMs can create task-linked sessions",
        )

    messaging = get_messaging_service(db)

    try:
        # Channel-existence check lives in the service so routes never touch
        # ChannelTable directly. Error detail matches the pre-refactor format
        # ("Channel '<slug>' not found") so clients parsing the message keep
        # working — NotFoundError's auto-message is "Channel not found: <slug>".
        await messaging.get_channel_by_slug_or_raise(data.channel_slug)
    except NotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Channel '{data.channel_slug}' not found",
        ) from e

    try:
        rel_type = SessionTaskRelationshipType(data.relationship_type)
    except ValueError:
        rel_type = SessionTaskRelationshipType.DISCUSSION

    req = SessionForTasksCreate(
        task_ids=data.task_ids,
        channel_slug=data.channel_slug,
        group_id=data.group_id,
        scope=data.scope,
        relationship_type=rel_type,
    )

    try:
        session_row, links = await messaging.create_session_for_tasks(req, agent_id)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except ConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e

    return SessionTaskLinksResponse(
        session=_session_to_response(session_row),
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
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except ConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e

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
    messaging = get_messaging_service(db)
    try:
        await messaging.get_session_or_raise(session_id)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    links = await messaging.get_tasks_for_session(session_id)
    return [_link_to_response(link) for link in links]
