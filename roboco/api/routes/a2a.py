"""
A2A (Agent-to-Agent) Protocol Routes

Implements Google's A2A protocol for agent interoperability.
See: https://a2a-protocol.org/latest/specification/

Endpoints:
- GET /.well-known/agent.json: System Agent Card
- GET /agents/{agent_id}/.well-known/agent.json: Per-agent Agent Card
- POST /api/a2a/message/send: Send message and create/update task
- POST /api/a2a/message/stream: Send message with SSE streaming
- GET /api/a2a/tasks/{task_id}: Get task state
- GET /api/a2a/tasks: List tasks
- POST /api/a2a/tasks/{task_id}/cancel: Cancel task
"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sse_starlette import EventSourceResponse

from roboco.api.deps import (
    CurrentAgentContext,
    CurrentAgentSlug,
    DbSession,
    require_ceo_role,
    require_pm_or_above,
)
from roboco.api.routes.v1._role_dep import require_any_authenticated_agent
from roboco.api.schemas.a2a_chat import (
    AdminConversationListResponse,
    AdminConversationSummaryResponse,
    AdminPairListResponse,
    AdminPairResponse,
    AdminReplyRequest,
    ConversationCloseRequest,
    ConversationCreateRequest,
    ConversationListResponse,
    ConversationResponse,
    ConversationSummaryResponse,
    InboxSummaryResponse,
    ListConversationsParams,
    ListMessagesParams,
    MessageCreateRequest,
    MessageListResponse,
    MessageResponse,
    PairListResponse,
    PairResponse,
)
from roboco.db.base import get_session_factory
from roboco.enforcement import A2AAccessDeniedError
from roboco.models.a2a import (
    A2AConversation,
    A2AConversationStatus,
    A2ATask,
    AgentCard,
    CancelTaskRequest,
    ListTasksResponse,
    SendMessageRequest,
)
from roboco.security import guard_deco, prompt_injection_validator
from roboco.services.a2a import A2AService
from roboco.utils.converters import require_uuid

# Router for A2A API endpoints (mounted at /api/a2a)
router = APIRouter()

# Router for well-known endpoints (mounted at root level)
wellknown_router = APIRouter()


# =============================================================================
# WELL-KNOWN ENDPOINTS (mounted at root)
# =============================================================================


@wellknown_router.get("/.well-known/agent.json")
async def get_system_agent_card() -> JSONResponse:
    """
    Get the system-level Agent Card.

    Per A2A specification, returns the agent's public identity and capabilities.
    """
    card = A2AService.build_system_agent_card()
    return JSONResponse(
        content=card.model_dump(by_alias=True, exclude_none=True),
        media_type="application/json",
    )


@wellknown_router.get("/agents/{agent_id}/.well-known/agent.json")
async def get_agent_card(
    agent_id: str,
    db: DbSession,
) -> JSONResponse:
    """
    Get Agent Card for a specific agent.

    Accepts either a UUID string or agent slug (e.g., "be-dev-1").
    """
    service = A2AService(db)
    card = await service.build_agent_card(agent_id)

    if card is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id}",
        )

    return JSONResponse(
        content=card.model_dump(by_alias=True, exclude_none=True),
        media_type="application/json",
    )


# =============================================================================
# A2A API ENDPOINTS (mounted at /api/a2a)
# =============================================================================


@router.post(
    "/message/send",
    dependencies=[require_any_authenticated_agent],
)
@guard_deco.rate_limit(requests=60, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(prompt_injection_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
@guard_deco.suspicious_detection(enabled=True)
async def send_message(
    request: SendMessageRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> dict[str, Any]:
    """
    Send an A2A message (fallback endpoint).

    This endpoint is used by the SDK Server when the target agent is offline.
    It creates a notification that the orchestrator dispatcher will pick up
    to spawn the target agent.

    DOES NOT create tasks. Creates notifications only.
    task_id is REQUIRED - A2A is about existing tasks.
    """
    service = A2AService(db)
    message = request.message
    task_id_str = message.task_id

    # task_id is REQUIRED for A2A
    if not task_id_str:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "TASK_ID_REQUIRED",
                "message": "A2A requests must include task_id.",
                "hint": "A2A is for communication about existing tasks. "
                "Ask your PM to create the task first if you need a new "
                "task (PMs author tasks via the planning/delegation flow).",
            },
        )

    # Check if this is a response to an existing A2A conversation
    metadata = request.metadata or {}
    is_response = metadata.get("is_response", False)

    if is_response:
        # The responder is the AUTHENTICATED caller — never a client-supplied
        # metadata.from_agent, which any caller could spoof to impersonate
        # anyone (e.g. from_agent='ceo') in the task's notes and in the
        # spawn/notification routed back to the original requester (#116).
        responder = agent.slug
        try:
            await service.update_task_from_message(
                task_id_str, message, responder_agent=responder
            )
        except ValueError as e:
            error_msg = str(e)
            if "Invalid task ID" in error_msg:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=error_msg,
                ) from None
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=error_msg,
            ) from None
        await db.commit()
        return {"status": "response_sent", "task_id": task_id_str}

    # Create A2A notification (NOT a task) and route to agent
    try:
        result = await service.create_a2a_notification(request)
    except ValueError as e:
        error_str = str(e)
        # Check if it's a permission error (includes "Hint:")
        if "Hint:" in error_str:
            parts = error_str.split(" Hint: ", 1)
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "A2A_NOT_PERMITTED",
                    "message": parts[0],
                    "hint": parts[1] if len(parts) > 1 else "",
                },
            ) from None
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "A2A_ERROR", "message": error_str},
        ) from None

    await db.commit()
    return {"status": "success", "a2a_request": result}


@router.post(
    "/message/stream",
    dependencies=[require_any_authenticated_agent],
)
@guard_deco.rate_limit(requests=60, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(prompt_injection_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
@guard_deco.suspicious_detection(enabled=True)
async def send_message_stream(
    request: Request,
    body: SendMessageRequest,
    db: DbSession,
) -> EventSourceResponse:
    """
    Send a message with SSE streaming response.

    Per A2A specification, this endpoint streams task updates in real-time
    as the task progresses through its lifecycle.

    Returns Server-Sent Events with task state updates.
    """
    service = A2AService(db)
    message = body.message

    async def generate_task_events() -> AsyncGenerator[dict[str, Any]]:
        """Generate SSE events for task lifecycle."""
        task_id_str = message.task_id

        if task_id_str:
            # Get existing task
            a2a_task = await service.get_task(task_id_str)

            if a2a_task is None:
                yield {
                    "event": "error",
                    "data": f"Task not found: {task_id_str}",
                }
                return

            # Send initial task state
            yield {
                "event": "task.status",
                "id": a2a_task.id,
                "data": a2a_task.model_dump_json(by_alias=True),
            }

            # Stream updates while task is in progress
            poll_count = 0
            max_polls = 60  # Poll for up to 60 iterations (5 minutes at 5s interval)

            while poll_count < max_polls:
                if await request.is_disconnected():
                    break

                await asyncio.sleep(5)
                poll_count += 1

                # Refresh task state
                a2a_task = await service.get_task(task_id_str)
                if a2a_task is None:
                    break

                yield {
                    "event": "task.status",
                    "id": f"{a2a_task.id}-{poll_count}",
                    "data": a2a_task.model_dump_json(by_alias=True),
                }

                # Stop if task is in terminal state
                if a2a_task.status.state in ["completed", "canceled"]:
                    yield {
                        "event": "task.complete",
                        "id": f"{a2a_task.id}-final",
                        "data": a2a_task.model_dump_json(by_alias=True),
                    }
                    break
        else:
            # New task - send creation event
            yield {
                "event": "task.creating",
                "data": "Creating new task from message...",
            }

            # Note: Full task creation logic would go here
            yield {
                "event": "error",
                "data": "Task creation via streaming not yet implemented",
            }

    return EventSourceResponse(
        generate_task_events(),
        ping=15,
    )


@router.get(
    "/tasks/{task_id}/subscribe",
    dependencies=[require_any_authenticated_agent],
)
async def subscribe_to_task(
    request: Request,
    task_id: str,
) -> EventSourceResponse:
    """
    Subscribe to task updates via SSE.

    Opens a persistent connection that streams task state changes
    until the task reaches a terminal state or client disconnects.

    Each poll opens a SHORT-LIVED session via ``get_session_factory`` and
    closes it before the next ``asyncio.sleep`` — never holding one asyncpg
    connection across the full SSE lifetime (up to 1 hour / 720 polls). The
    route takes no ``db: DbSession`` for the same reason.
    """
    session_factory = get_session_factory()

    # Validate task exists with a short-lived session (released immediately).
    async with session_factory() as session:
        a2a_task = await A2AService(session).get_task(task_id)
    if a2a_task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task not found: {task_id}",
        )

    async def generate_updates() -> AsyncGenerator[dict[str, Any]]:
        """Stream task updates — one short-lived session per poll."""
        poll_count = 0
        max_polls = 720  # 1 hour at 5s interval
        last_state = None

        while poll_count < max_polls:
            if await request.is_disconnected():
                break

            # Refresh task state from a per-poll session released before the
            # sleep — never held across the poll interval, so the asyncpg pool
            # is free between queries.
            async with session_factory() as session:
                task = await A2AService(session).get_task(task_id)
            if task is None:
                break

            current_state = task.status.state

            # Only send update if status changed
            if current_state != last_state:
                yield {
                    "event": "task.status",
                    "id": f"{task_id}-{poll_count}",
                    "data": task.model_dump_json(by_alias=True),
                }
                last_state = current_state

                # Stop if terminal
                if current_state in ["completed", "canceled"]:
                    yield {
                        "event": "task.complete",
                        "id": f"{task_id}-final",
                        "data": task.model_dump_json(by_alias=True),
                    }
                    break

            await asyncio.sleep(5)
            poll_count += 1

    return EventSourceResponse(
        generate_updates(),
        ping=15,
    )


@router.get("/tasks/{task_id}")
async def get_task(
    task_id: str,
    db: DbSession,
    _agent: CurrentAgentContext,
    _history_length: int | None = Query(
        None, alias="historyLength", description="Number of history turns to include"
    ),
) -> A2ATask:
    """
    Get the state of an A2A task.

    Returns task details including status, artifacts, and optionally history.
    """
    service = A2AService(db)
    task = await service.get_task(task_id)

    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task not found: {task_id}",
        )

    return task


@router.get("/tasks")
async def list_tasks(
    db: DbSession,
    _agent: CurrentAgentContext,
    page_size: int = Query(20, alias="pageSize", ge=1, le=100),
    page_token: str | None = Query(None, alias="pageToken"),
    _filter_str: str | None = Query(None, alias="filter"),
    order_by: str | None = Query(None, alias="orderBy"),
) -> ListTasksResponse:
    """
    List A2A tasks with pagination.

    Supports filtering and pagination via page tokens.
    """
    service = A2AService(db)

    # Handle pagination
    offset = 0
    if page_token:
        with contextlib.suppress(ValueError):
            offset = int(page_token)

    tasks, has_more = await service.list_tasks(
        page_size=page_size,
        offset=offset,
        order_by=order_by,
    )

    next_page_token = str(offset + page_size) if has_more else None

    return ListTasksResponse(
        tasks=tasks,
        next_page_token=next_page_token,
    )


@router.post(
    "/tasks/{task_id}/cancel",
    dependencies=[require_any_authenticated_agent],
)
@guard_deco.rate_limit(requests=10, window=60)
@guard_deco.content_type_filter(["application/json"])
async def cancel_task(
    task_id: str,
    db: DbSession,
    agent: CurrentAgentContext,
    request: CancelTaskRequest | None = None,
) -> A2ATask:
    """
    Cancel an A2A task.

    Transitions the task to cancelled state. PM/management-only — the service
    cascades the cancel to all non-terminal descendants, and the lifecycle rule
    (Any -> cancelled: PM roles only) must hold on this path too (#423).
    """
    require_pm_or_above(agent.role, action="cancel a task via A2A")
    service = A2AService(db)

    try:
        task = await service.cancel_task(
            task_id=task_id,
            reason=request.reason if request else None,
            agent_role=agent.role.value,
            actor_slug=agent.slug,
        )
    except ValueError as e:
        error_msg = str(e)
        if "Invalid task ID" in error_msg or "already in terminal" in error_msg:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error_msg,
            ) from None
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error_msg,
        ) from None

    await db.commit()
    return task


# =============================================================================
# AGENT DISCOVERY ENDPOINTS
# =============================================================================


@router.get("/agents")
async def discover_agents(
    db: DbSession,
    role: str | None = Query(None, description="Filter by agent role"),
    team: str | None = Query(None, description="Filter by team"),
    skill: str | None = Query(None, description="Filter by skill tag"),
) -> list[AgentCard]:
    """
    Discover agents matching criteria.

    Returns a list of AgentCards for agents that match the specified filters.
    This enables A2A clients to find agents with specific capabilities.
    """
    service = A2AService(db)
    return await service.discover_agents(
        role=role,
        team=team,
        skill_tag=skill,
    )


@router.get("/agents/{agent_id}/card")
async def get_agent_card_by_id(
    agent_id: str,
    db: DbSession,
) -> AgentCard:
    """
    Get Agent Card for a specific agent by ID or slug.

    Alternative to the /.well-known/agent.json endpoint for programmatic access.
    """
    service = A2AService(db)
    card = await service.build_agent_card(agent_id)

    if card is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id}",
        )

    return card


# =============================================================================
# PERSISTENT A2A CHAT ENDPOINTS
# =============================================================================
# These endpoints manage persistent conversations stored in the database.


@router.get("/chat/inbox")
async def get_inbox_summary(
    db: DbSession,
    agent_slug: CurrentAgentSlug,
) -> InboxSummaryResponse:
    """Get A2A inbox summary for current agent."""
    service = A2AService(db)
    summary = await service.get_inbox_summary(agent_slug)
    return InboxSummaryResponse(
        total_unread=summary.total_unread,
        conversations_with_unread=summary.conversations_with_unread,
        pending_responses=summary.pending_responses,
        unanswered_requests=summary.unanswered_requests,
    )


@router.get("/chat/pairs")
async def list_pairs(
    db: DbSession,
    agent_slug: CurrentAgentSlug,
) -> PairListResponse:
    """List unique agent pairs for current agent."""
    service = A2AService(db)
    pairs = await service.list_pairs(agent_slug)
    return PairListResponse(
        items=[
            PairResponse(
                agent_a=p.agent_a,
                agent_b=p.agent_b,
                conversation_count=p.conversation_count,
                total_unread=p.total_unread,
                last_activity=p.last_activity,
            )
            for p in pairs
        ],
        total=len(pairs),
    )


@router.get("/chat/conversations")
async def list_chat_conversations(
    db: DbSession,
    agent_slug: CurrentAgentSlug,
    params: Annotated[ListConversationsParams, Depends()],
) -> ConversationListResponse:
    """List A2A conversations for current agent."""
    service = A2AService(db)

    status_filter = None
    if params.status:
        status_filter = A2AConversationStatus(params.status)

    conversations = await service.list_conversations(
        agent_slug=agent_slug,
        status=status_filter,
        with_agent=params.with_agent,
        task_id=params.task_id,
        limit=params.limit,
    )

    return ConversationListResponse(
        items=[
            ConversationSummaryResponse(
                id=require_uuid(c.id),
                other_agent=c.other_agent,
                topic=c.topic,
                task_id=require_uuid(c.task_id) if c.task_id else None,
                status=c.status,
                message_count=c.message_count,
                unread_count=c.unread_count,
                last_message_at=c.last_message_at,
                last_message_preview=c.last_message_preview,
            )
            for c in conversations
        ],
        total=len(conversations),
    )


@router.post("/chat/conversations", status_code=status.HTTP_201_CREATED)
@guard_deco.rate_limit(requests=60, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(prompt_injection_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
@guard_deco.suspicious_detection(enabled=True)
async def create_conversation(
    db: DbSession,
    agent_slug: CurrentAgentSlug,
    data: ConversationCreateRequest,
) -> ConversationResponse:
    """Start a new A2A conversation."""
    service = A2AService(db)

    try:
        conv = await service.get_or_create_conversation(
            agent_a=agent_slug,
            agent_b=data.target_agent,
            topic=data.topic,
            task_id=data.task_id,
        )
    except A2AAccessDeniedError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "A2A_ACCESS_DENIED",
                "message": e.message,
                "route_hint": e.route_hint,
            },
        ) from None

    # Send initial message
    await service.send_chat_message(
        conversation_id=require_uuid(conv.id),
        from_agent=agent_slug,
        content=data.initial_message,
        options={"requires_response": data.requires_response},
    )

    # Refresh conversation to get updated stats
    refreshed = await service.get_conversation(require_uuid(conv.id), agent_slug)
    if refreshed is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve created conversation",
        )

    await db.commit()

    return ConversationResponse(
        id=require_uuid(refreshed.id),
        agent_a=refreshed.agent_a,
        agent_b=refreshed.agent_b,
        topic=refreshed.topic,
        task_id=require_uuid(refreshed.task_id) if refreshed.task_id else None,
        status=refreshed.status,
        resolution=refreshed.resolution,
        message_count=refreshed.message_count,
        unread_by_a=refreshed.unread_by_a,
        unread_by_b=refreshed.unread_by_b,
        created_at=refreshed.created_at,
        updated_at=refreshed.updated_at,
        last_message_at=refreshed.last_message_at,
    )


@router.get("/chat/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    db: DbSession,
    agent_slug: CurrentAgentSlug,
) -> ConversationResponse:
    """Get a specific conversation."""
    service = A2AService(db)
    conv = await service.get_conversation(require_uuid(conversation_id), agent_slug)

    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation not found: {conversation_id}",
        )

    return ConversationResponse(
        id=require_uuid(conv.id),
        agent_a=conv.agent_a,
        agent_b=conv.agent_b,
        topic=conv.topic,
        task_id=require_uuid(conv.task_id) if conv.task_id else None,
        status=conv.status,
        resolution=conv.resolution,
        message_count=conv.message_count,
        unread_by_a=conv.unread_by_a,
        unread_by_b=conv.unread_by_b,
        created_at=conv.created_at,
        updated_at=conv.updated_at,
        last_message_at=conv.last_message_at,
    )


@router.post("/chat/conversations/{conversation_id}/close")
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
async def close_conversation(
    conversation_id: str,
    db: DbSession,
    agent_slug: CurrentAgentSlug,
    data: ConversationCloseRequest | None = None,
) -> None:
    """Close a conversation."""
    service = A2AService(db)

    try:
        await service.close_conversation(
            conversation_id=require_uuid(conversation_id),
            agent_slug=agent_slug,
            resolution=data.resolution if data else None,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from None

    await db.commit()


@router.get("/chat/conversations/{conversation_id}/messages")
async def list_chat_messages(
    conversation_id: str,
    db: DbSession,
    agent_slug: CurrentAgentSlug,
    params: Annotated[ListMessagesParams, Depends()],
) -> MessageListResponse:
    """Get messages in a conversation."""
    service = A2AService(db)

    messages = await service.get_messages(
        conversation_id=require_uuid(conversation_id),
        agent_slug=agent_slug,
        limit=params.limit + 1,  # +1 to detect has_more
        before=params.before,
    )

    has_more = len(messages) > params.limit
    if has_more:
        messages = messages[: params.limit]

    return MessageListResponse(
        items=[
            MessageResponse(
                id=require_uuid(m.id),
                conversation_id=require_uuid(m.conversation_id),
                from_agent=m.from_agent,
                content=m.content,
                message_kind=m.message_kind,
                response_to_id=(
                    require_uuid(m.response_to_id) if m.response_to_id else None
                ),
                requires_response=m.requires_response,
                read_at=m.read_at,
                created_at=m.created_at,
                edited_at=m.edited_at,
            )
            for m in messages
        ],
        total=len(messages),
        has_more=has_more,
    )


@router.post(
    "/chat/conversations/{conversation_id}/messages",
    status_code=status.HTTP_201_CREATED,
)
@guard_deco.rate_limit(requests=60, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(prompt_injection_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
@guard_deco.suspicious_detection(enabled=True)
async def send_chat_message(
    conversation_id: str,
    db: DbSession,
    agent_slug: CurrentAgentSlug,
    data: MessageCreateRequest,
) -> MessageResponse:
    """Send a message in a conversation."""
    service = A2AService(db)

    try:
        msg = await service.send_chat_message(
            conversation_id=require_uuid(conversation_id),
            from_agent=agent_slug,
            content=data.content,
            options={
                "message_kind": data.message_kind,
                "response_to_id": data.response_to_id,
                "requires_response": data.requires_response,
            },
        )
    except A2AAccessDeniedError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "A2A_ACCESS_DENIED",
                "message": e.message,
                "route_hint": e.route_hint,
            },
        ) from None
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from None

    await db.commit()

    return MessageResponse(
        id=require_uuid(msg.id),
        conversation_id=require_uuid(msg.conversation_id),
        from_agent=msg.from_agent,
        content=msg.content,
        message_kind=msg.message_kind,
        response_to_id=require_uuid(msg.response_to_id) if msg.response_to_id else None,
        requires_response=msg.requires_response,
        read_at=msg.read_at,
        created_at=msg.created_at,
        edited_at=msg.edited_at,
    )


@router.post("/chat/conversations/{conversation_id}/read", status_code=204)
@guard_deco.rate_limit(requests=60, window=60)
async def mark_read(
    conversation_id: str,
    db: DbSession,
    agent_slug: CurrentAgentSlug,
) -> None:
    """Mark all messages in conversation as read."""
    service = A2AService(db)
    await service.mark_read(require_uuid(conversation_id), agent_slug)
    await db.commit()


@router.get("/chat/tasks/{task_id}/conversations")
async def get_task_conversations(
    task_id: str,
    db: DbSession,
    agent_slug: CurrentAgentSlug,
) -> ConversationListResponse:
    """Get A2A conversations linked to a specific task."""
    service = A2AService(db)

    conversations = await service.list_conversations(
        agent_slug=agent_slug,
        task_id=require_uuid(task_id),
    )

    return ConversationListResponse(
        items=[
            ConversationSummaryResponse(
                id=require_uuid(c.id),
                other_agent=c.other_agent,
                topic=c.topic,
                task_id=require_uuid(c.task_id) if c.task_id else None,
                status=c.status,
                message_count=c.message_count,
                unread_count=c.unread_count,
                last_message_at=c.last_message_at,
                last_message_preview=c.last_message_preview,
            )
            for c in conversations
        ],
        total=len(conversations),
    )


# =============================================================================
# ADMIN / LIVE VIEW ENDPOINTS (CEO-only)
# =============================================================================
# The CEO's org-wide A2A live view: unlike the participant-scoped endpoints
# above, these read across every conversation regardless of who's a party to
# it, and let the CEO chime into an existing thread as itself.


def _require_ceo(agent: CurrentAgentContext) -> None:
    require_ceo_role(agent.role, action="view or reply to the A2A live view")


def _resolve_reply_target(conv: A2AConversation, to_agent: str) -> None:
    """Validate the CEO's reply target against the pairwise conversation.

    Raises the appropriate 400 HTTPException — kept out of the route handler
    to keep its cyclomatic complexity low. A2A conversations are strictly
    pairwise (no N-party thread), so the CEO must address one of the two
    real participants; A2A is also scoped to a task by construction
    (A2AService.send requires task_id), so an untethered conversation can't
    be replied into via this path.
    """
    if to_agent not in (conv.agent_a, conv.agent_b):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"{to_agent} is not a participant in this conversation "
                f"(participants: {conv.agent_a}, {conv.agent_b})"
            ),
        )
    if conv.task_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Conversation has no linked task_id — A2A requires one",
        )


@router.get("/chat/admin/conversations")
async def list_admin_conversations(
    db: DbSession,
    agent: CurrentAgentContext,
    limit: int = Query(50, ge=1, le=100),
) -> AdminConversationListResponse:
    """CEO-only: list conversations across every agent pair, most-recent-first."""
    _require_ceo(agent)
    service = A2AService(db)
    conversations = await service.list_conversations_admin(limit)

    return AdminConversationListResponse(
        items=[
            AdminConversationSummaryResponse(
                id=require_uuid(c.id),
                agent_a=c.agent_a,
                agent_b=c.agent_b,
                topic=c.topic,
                task_id=require_uuid(c.task_id) if c.task_id else None,
                status=c.status,
                message_count=c.message_count,
                last_message_at=c.last_message_at,
                last_message_preview=c.last_message_preview,
                created_at=c.created_at,
                updated_at=c.updated_at,
            )
            for c in conversations
        ],
        total=len(conversations),
    )


@router.get("/chat/admin/pairs")
async def list_admin_pairs(
    db: DbSession,
    agent: CurrentAgentContext,
) -> AdminPairListResponse:
    """CEO-only: the org-chart switchboard.

    Every agent pair allowed to A2A directly per the static
    ``agents_config.can_a2a_direct`` matrix (>=1 direction), joined with each
    pair's representative conversation stats when one exists — the pair
    cards the panel groups into sections (each cell, the PM chain, board).
    """
    _require_ceo(agent)
    service = A2AService(db)
    pairs = await service.list_admin_pairs()

    return AdminPairListResponse(
        items=[
            AdminPairResponse(
                agent_a=p.agent_a,
                role_a=p.role_a,
                team_a=p.team_a,
                agent_b=p.agent_b,
                role_b=p.role_b,
                team_b=p.team_b,
                group_key=p.group_key,
                conversation_id=(
                    require_uuid(p.conversation_id) if p.conversation_id else None
                ),
                last_message_at=p.last_message_at,
                message_count=p.message_count,
            )
            for p in pairs
        ],
        total=len(pairs),
    )


@router.get("/chat/admin/conversations/{conversation_id}/messages")
async def list_admin_chat_messages(
    conversation_id: str,
    db: DbSession,
    agent: CurrentAgentContext,
    limit: int = Query(100, ge=1, le=500),
    before: datetime | None = None,
) -> MessageListResponse:
    """CEO-only: read any conversation's transcript, participant or not."""
    _require_ceo(agent)
    service = A2AService(db)

    messages = await service.get_messages_admin(
        conversation_id=require_uuid(conversation_id),
        limit=limit + 1,  # +1 to detect has_more
        before=before,
    )

    has_more = len(messages) > limit
    if has_more:
        messages = messages[:limit]

    return MessageListResponse(
        items=[
            MessageResponse(
                id=require_uuid(m.id),
                conversation_id=require_uuid(m.conversation_id),
                from_agent=m.from_agent,
                content=m.content,
                message_kind=m.message_kind,
                response_to_id=(
                    require_uuid(m.response_to_id) if m.response_to_id else None
                ),
                requires_response=m.requires_response,
                read_at=m.read_at,
                created_at=m.created_at,
                edited_at=m.edited_at,
            )
            for m in messages
        ],
        total=len(messages),
        has_more=has_more,
    )


@router.post(
    "/chat/admin/conversations/{conversation_id}/reply",
    status_code=status.HTTP_201_CREATED,
)
@guard_deco.rate_limit(requests=60, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(prompt_injection_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
@guard_deco.suspicious_detection(enabled=True)
async def reply_as_ceo(
    conversation_id: str,
    db: DbSession,
    agent: CurrentAgentContext,
    data: AdminReplyRequest,
) -> MessageResponse:
    """CEO-only: interject into an existing A2A conversation as itself.

    A one-directional interjection, not a CEO<->agent DM: the message is
    inserted into THIS conversation (readable by both participants) and
    addressed to one of its two real participants via ``interject_as_ceo``.
    """
    _require_ceo(agent)
    service = A2AService(db)

    conv = await service.get_conversation_admin(require_uuid(conversation_id))
    if conv is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Conversation not found: {conversation_id}",
        )
    _resolve_reply_target(conv, data.to_agent)

    msg = await service.interject_as_ceo(
        conversation_id=require_uuid(conversation_id),
        to_agent=data.to_agent,
        content=data.content,
        skill=data.skill,
    )

    await db.commit()

    return MessageResponse(
        id=require_uuid(msg.id),
        conversation_id=require_uuid(msg.conversation_id),
        from_agent=msg.from_agent,
        content=msg.content,
        message_kind=msg.message_kind,
        response_to_id=require_uuid(msg.response_to_id) if msg.response_to_id else None,
        requires_response=msg.requires_response,
        read_at=msg.read_at,
        created_at=msg.created_at,
        edited_at=msg.edited_at,
    )
