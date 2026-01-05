"""
A2A (Agent-to-Agent) Protocol Routes

Implements Google's A2A protocol for agent interoperability.
See: https://a2a-protocol.org/latest/specification/

Endpoints:
- GET /.well-known/agent.json: System Agent Card
- GET /agents/{agent_id}/.well-known/agent.json: Per-agent Agent Card
- POST /api/v1/a2a/message/send: Send message and create/update task
- POST /api/v1/a2a/message/stream: Send message with SSE streaming
- GET /api/v1/a2a/tasks/{task_id}: Get task state
- GET /api/v1/a2a/tasks: List tasks
- POST /api/v1/a2a/tasks/{task_id}/cancel: Cancel task
"""

import asyncio
import contextlib
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sse_starlette import EventSourceResponse

from roboco.api.deps import DbSession
from roboco.models.a2a import (
    A2ATask,
    AgentCard,
    CancelTaskRequest,
    ListTasksResponse,
    SendMessageRequest,
)
from roboco.services.a2a import A2AService

# Router for A2A API endpoints (mounted at /api/v1/a2a)
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
# A2A API ENDPOINTS (mounted at /api/v1/a2a)
# =============================================================================


@router.post("/message/send")
async def send_message(
    request: SendMessageRequest,
    db: DbSession,
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
                "Use roboco_task_create() first if you need a new task.",
            },
        )

    # Check if this is a response to an existing A2A conversation
    metadata = request.metadata or {}
    is_response = metadata.get("is_response", False)

    if is_response:
        # Update existing task with response message
        responder = metadata.get("from_agent")
        try:
            await service.update_task_from_message(task_id_str, message, responder)
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


@router.post("/message/stream")
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


@router.get("/tasks/{task_id}/subscribe")
async def subscribe_to_task(
    request: Request,
    task_id: str,
    db: DbSession,
) -> EventSourceResponse:
    """
    Subscribe to task updates via SSE.

    Opens a persistent connection that streams task state changes
    until the task reaches a terminal state or client disconnects.
    """
    service = A2AService(db)

    # Validate task exists
    a2a_task = await service.get_task(task_id)
    if a2a_task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task not found: {task_id}",
        )

    async def generate_updates() -> AsyncGenerator[dict[str, Any]]:
        """Stream task updates."""
        poll_count = 0
        max_polls = 720  # 1 hour at 5s interval
        last_state = None

        while poll_count < max_polls:
            if await request.is_disconnected():
                break

            # Refresh task state from DB
            task = await service.get_task(task_id)
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


@router.post("/tasks/{task_id}/cancel")
async def cancel_task(
    task_id: str,
    db: DbSession,
    request: CancelTaskRequest | None = None,
) -> A2ATask:
    """
    Cancel an A2A task.

    Transitions the task to cancelled state.
    """
    service = A2AService(db)

    try:
        task = await service.cancel_task(
            task_id=task_id,
            reason=request.reason if request else None,
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
