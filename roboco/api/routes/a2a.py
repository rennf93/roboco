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
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sse_starlette import EventSourceResponse

from roboco.api.deps import DbSession
from roboco.config import settings
from roboco.db.tables import AgentTable, TaskTable
from roboco.models.a2a import (
    A2AArtifact,
    A2AMessage,
    A2ATask,
    A2ATaskStatus,
    AgentCapabilities,
    AgentCard,
    AgentProvider,
    AgentSkill,
    CancelTaskRequest,
    ListTasksResponse,
    SecurityScheme,
    SendMessageRequest,
    SendMessageResponse,
    TextPart,
    task_status_to_a2a_state,
)
from roboco.models.base import TaskStatus

# Router for A2A API endpoints (mounted at /api/v1/a2a)
router = APIRouter()

# Router for well-known endpoints (mounted at root level)
wellknown_router = APIRouter()


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def _get_service_endpoint() -> str:
    """Build service endpoint URL from settings."""
    connect_host = "127.0.0.1" if settings.host == "0.0.0.0" else settings.host
    return f"http://{connect_host}:{settings.port}"


def _build_system_agent_card() -> AgentCard:
    """Build the system-level Agent Card for RoboCo."""
    return AgentCard(
        id="roboco-system",
        name="RoboCo System",
        description=(
            "RoboCo is an AI Agentic Company - a virtual organization of AI agents "
            "designed to operate as a complete software development workforce."
        ),
        provider=AgentProvider(
            organization="RoboCo",
            url="https://github.com/roboco",
        ),
        protocol_version="1.0",
        service_endpoint=f"{_get_service_endpoint()}/api/v1/a2a",
        version=settings.app_version,
        capabilities=AgentCapabilities(
            streaming=True,  # We support SSE
            push_notifications=False,  # Not implemented yet
            state_transition_history=True,  # We track task history
        ),
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "application/json"],
        skills=[
            AgentSkill(
                id="software-development",
                name="Software Development",
                description="Full-stack software development with AI agents",
                tags=["development", "coding", "qa", "documentation"],
            ),
            AgentSkill(
                id="task-management",
                name="Task Management",
                description="Create and manage development tasks",
                tags=["tasks", "kanban", "planning"],
            ),
            AgentSkill(
                id="code-review",
                name="Code Review",
                description="Review and quality assurance of code",
                tags=["qa", "review", "testing"],
            ),
        ],
        documentation_url="https://github.com/roboco/docs",
        security_schemes={
            "bearerAuth": SecurityScheme(type="http", scheme="bearer"),
        },
        security=[{"bearerAuth": []}],
    )


async def _build_agent_card(agent: AgentTable) -> AgentCard:
    """Build an Agent Card for a specific agent."""
    agent_id = str(agent.id)
    agent_slug = agent.slug

    # Map role to skills
    role_skills: dict[str, list[AgentSkill]] = {
        "developer": [
            AgentSkill(
                id="coding",
                name="Code Development",
                description="Write and implement code",
                tags=["development", "coding"],
            ),
            AgentSkill(
                id="debugging",
                name="Debugging",
                description="Debug and fix code issues",
                tags=["debugging", "troubleshooting"],
            ),
        ],
        "qa": [
            AgentSkill(
                id="testing",
                name="Testing",
                description="Test code and verify quality",
                tags=["qa", "testing"],
            ),
            AgentSkill(
                id="review",
                name="Code Review",
                description="Review code for quality and issues",
                tags=["qa", "review"],
            ),
        ],
        "documenter": [
            AgentSkill(
                id="documentation",
                name="Documentation",
                description="Write technical documentation",
                tags=["documentation", "writing"],
            ),
        ],
        "cell_pm": [
            AgentSkill(
                id="coordination",
                name="Task Coordination",
                description="Coordinate tasks within the cell",
                tags=["management", "coordination"],
            ),
        ],
        "main_pm": [
            AgentSkill(
                id="planning",
                name="Project Planning",
                description="Plan and coordinate across cells",
                tags=["management", "planning"],
            ),
        ],
    }

    skills = role_skills.get(agent.role, [])

    return AgentCard(
        id=agent_id,
        name=agent.name,
        description=f"{agent.name} - {agent.role} agent in RoboCo",
        provider=AgentProvider(
            organization="RoboCo",
            url="https://github.com/roboco",
        ),
        protocol_version="1.0",
        service_endpoint=f"{_get_service_endpoint()}/api/v1/a2a",
        version=settings.app_version,
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=False,
            state_transition_history=True,
        ),
        default_input_modes=["text/plain", "application/json"],
        default_output_modes=["text/plain", "application/json"],
        skills=skills,
        metadata={
            "slug": agent_slug,
            "role": agent.role,
            "team": agent.team,
        },
        security_schemes={
            "bearerAuth": SecurityScheme(type="http", scheme="bearer"),
        },
        security=[{"bearerAuth": []}],
    )


def _task_to_a2a(task: TaskTable) -> A2ATask:
    """Convert a RoboCo TaskTable to A2A Task."""
    task_id = str(task.id)

    # Build status - get status value as string
    if hasattr(task.status, "value"):
        status_value = task.status.value
    else:
        status_value = str(task.status)
    a2a_state = task_status_to_a2a_state(status_value)
    status_message = None
    if task.dev_notes:
        status_message = A2AMessage(
            role="agent",
            parts=[TextPart(text=task.dev_notes)],
            task_id=task_id,
        )

    a2a_status = A2ATaskStatus(
        state=a2a_state,
        message=status_message,
        timestamp=task.updated_at or task.created_at,
    )

    # Build artifacts from task outputs (if any)
    artifacts: list[A2AArtifact] = []

    # Build metadata from task fields
    metadata: dict[str, Any] = {
        "roboco_status": status_value,
        "priority": task.priority,
        "team": task.team,
    }
    if task.assigned_to:
        metadata["assigned_to"] = str(task.assigned_to)
    if task.parent_task_id:
        metadata["parent_task_id"] = str(task.parent_task_id)

    return A2ATask(
        id=task_id,
        context_id=task_id,  # Use task_id as context_id
        status=a2a_status,
        artifacts=artifacts,
        history=[],  # Would need to load from message history
        metadata=metadata,
    )


# =============================================================================
# WELL-KNOWN ENDPOINTS (mounted at root)
# =============================================================================


@wellknown_router.get("/.well-known/agent.json")
async def get_system_agent_card() -> JSONResponse:
    """
    Get the system-level Agent Card.

    Per A2A specification, returns the agent's public identity and capabilities.
    """
    card = _build_system_agent_card()
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
    # Try to parse as UUID first
    try:
        uuid = UUID(agent_id)
        result = await db.execute(select(AgentTable).where(AgentTable.id == uuid))
    except ValueError:
        # Not a UUID, try slug lookup
        result = await db.execute(
            select(AgentTable).where(AgentTable.slug == agent_id)
        )

    agent = result.scalar_one_or_none()

    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id}",
        )

    card = await _build_agent_card(agent)
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
) -> SendMessageResponse:
    """
    Send a message to create or update an A2A task.

    This is the primary A2A interaction endpoint. Messages sent here
    create new tasks or continue existing conversations.
    """
    message = request.message

    # Extract task_id from message if present
    task_id_str = message.task_id

    if task_id_str:
        # Update existing task
        try:
            task_uuid = UUID(task_id_str)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid task ID: {task_id_str}",
            ) from None

        result = await db.execute(
            select(TaskTable).where(TaskTable.id == task_uuid)
        )
        task = result.scalar_one_or_none()

        if task is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task not found: {task_id_str}",
            )

        # Update task dev_notes with new message
        text_parts = [p for p in message.parts if p.type == "text"]
        if text_parts:
            text_part = text_parts[0]
            if hasattr(text_part, "text"):
                new_text = text_part.text
                if task.dev_notes:
                    task.dev_notes = f"{task.dev_notes}\n\n{new_text}"
                else:
                    task.dev_notes = new_text

        await db.commit()
        await db.refresh(task)

    else:
        # Create new task from message
        # Note: For proper implementation, this should use TaskService
        # which handles session creation and proper agent context
        text_parts = [p for p in message.parts if p.type == "text"]
        title = "A2A Task"
        description = ""

        if text_parts:
            text_part = text_parts[0]
            if hasattr(text_part, "text"):
                first_text = text_part.text
                # Use first line as title, rest as description
                lines = first_text.split("\n", 1)
                title = lines[0][:200]  # Truncate title
                description = lines[1] if len(lines) > 1 else first_text

        # Get a system agent to use as creator
        # In production, this should come from authenticated context
        result = await db.execute(
            select(AgentTable).where(AgentTable.role == "main_pm").limit(1)
        )
        system_agent = result.scalar_one_or_none()

        if system_agent is None:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="No system agent available to create tasks",
            )

        from roboco.models.base import Team

        task = TaskTable(
            title=title,
            description=description,
            acceptance_criteria=["Task completed as specified"],
            status=TaskStatus.PENDING,
            priority=5,
            team=Team.BACKEND,
            created_by=system_agent.id,
        )
        db.add(task)
        await db.commit()
        await db.refresh(task)

    return SendMessageResponse(task=_task_to_a2a(task))


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
    message = body.message

    async def generate_task_events() -> AsyncGenerator[dict[str, Any]]:
        """Generate SSE events for task lifecycle."""
        # Create or get task
        task_id_str = message.task_id

        if task_id_str:
            # Get existing task
            try:
                task_uuid = UUID(task_id_str)
            except ValueError:
                yield {
                    "event": "error",
                    "data": f"Invalid task ID: {task_id_str}",
                }
                return

            result = await db.execute(
                select(TaskTable).where(TaskTable.id == task_uuid)
            )
            task = result.scalar_one_or_none()

            if task is None:
                yield {
                    "event": "error",
                    "data": f"Task not found: {task_id_str}",
                }
                return

            # Send initial task state
            a2a_task = _task_to_a2a(task)
            yield {
                "event": "task.status",
                "id": str(task.id),
                "data": a2a_task.model_dump_json(by_alias=True),
            }

            # Stream updates while task is in progress
            poll_count = 0
            max_polls = 60  # Poll for up to 60 iterations (5 minutes at 5s interval)

            while poll_count < max_polls:
                # Check for client disconnect
                if await request.is_disconnected():
                    break

                await asyncio.sleep(5)  # Poll interval
                poll_count += 1

                # Refresh task state
                await db.refresh(task)

                # Get current status
                if hasattr(task.status, "value"):
                    current_status = task.status.value
                else:
                    current_status = str(task.status)

                # Send update
                a2a_task = _task_to_a2a(task)
                yield {
                    "event": "task.status",
                    "id": f"{task.id}-{poll_count}",
                    "data": a2a_task.model_dump_json(by_alias=True),
                }

                # Stop if task is in terminal state
                if current_status in ["completed", "cancelled"]:
                    yield {
                        "event": "task.complete",
                        "id": f"{task.id}-final",
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
            # For now, send a placeholder
            yield {
                "event": "error",
                "data": "Task creation via streaming not yet implemented",
            }

    return EventSourceResponse(
        generate_task_events(),
        ping=15,  # Keep connection alive every 15 seconds
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
    try:
        task_uuid = UUID(task_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid task ID: {task_id}",
        ) from None

    result = await db.execute(select(TaskTable).where(TaskTable.id == task_uuid))
    task = result.scalar_one_or_none()

    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task not found: {task_id}",
        )

    async def generate_updates() -> AsyncGenerator[dict[str, Any]]:
        """Stream task updates."""
        poll_count = 0
        max_polls = 720  # 1 hour at 5s interval
        last_status = None

        while poll_count < max_polls:
            if await request.is_disconnected():
                break

            # Refresh task state from DB
            await db.refresh(task)

            # Get current status
            if hasattr(task.status, "value"):
                current_status = task.status.value
            else:
                current_status = str(task.status)

            # Only send update if status changed
            if current_status != last_status:
                a2a_task = _task_to_a2a(task)
                yield {
                    "event": "task.status",
                    "id": f"{task_id}-{poll_count}",
                    "data": a2a_task.model_dump_json(by_alias=True),
                }
                last_status = current_status

                # Stop if terminal
                if current_status in ["completed", "cancelled"]:
                    yield {
                        "event": "task.complete",
                        "id": f"{task_id}-final",
                        "data": a2a_task.model_dump_json(by_alias=True),
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
    try:
        task_uuid = UUID(task_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid task ID: {task_id}",
        ) from None

    result = await db.execute(select(TaskTable).where(TaskTable.id == task_uuid))
    task = result.scalar_one_or_none()

    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task not found: {task_id}",
        )

    return _task_to_a2a(task)


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
    query = select(TaskTable)

    # Apply ordering
    if order_by:
        if order_by == "created_at desc":
            query = query.order_by(TaskTable.created_at.desc())
        elif order_by == "created_at asc":
            query = query.order_by(TaskTable.created_at.asc())
        else:
            query = query.order_by(TaskTable.created_at.desc())
    else:
        query = query.order_by(TaskTable.created_at.desc())

    # Handle pagination
    offset = 0
    if page_token:
        with contextlib.suppress(ValueError):
            offset = int(page_token)

    query = query.offset(offset).limit(page_size + 1)

    result = await db.execute(query)
    tasks = list(result.scalars().all())

    # Check if there are more results
    has_more = len(tasks) > page_size
    if has_more:
        tasks = tasks[:page_size]

    next_page_token = str(offset + page_size) if has_more else None

    return ListTasksResponse(
        tasks=[_task_to_a2a(t) for t in tasks],
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
    try:
        task_uuid = UUID(task_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid task ID: {task_id}",
        ) from None

    result = await db.execute(select(TaskTable).where(TaskTable.id == task_uuid))
    task = result.scalar_one_or_none()

    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task not found: {task_id}",
        )

    # Check if task can be cancelled
    if hasattr(task.status, "value"):
        status_value = task.status.value
    else:
        status_value = str(task.status)
    terminal_states = ["completed", "cancelled"]
    if status_value in terminal_states:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Task already in terminal state: {status_value}",
        )

    # Cancel the task
    task.status = TaskStatus.CANCELLED
    if request and request.reason:
        reason_text = f"Cancellation reason: {request.reason}"
        if task.dev_notes:
            task.dev_notes = f"{task.dev_notes}\n\n{reason_text}"
        else:
            task.dev_notes = reason_text

    await db.commit()
    await db.refresh(task)

    return _task_to_a2a(task)


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
    query = select(AgentTable)

    if role:
        query = query.where(AgentTable.role == role)
    if team:
        query = query.where(AgentTable.team == team)

    result = await db.execute(query)
    agents = result.scalars().all()

    # Build cards for all matching agents
    cards = []
    for agent in agents:
        card = await _build_agent_card(agent)
        cards.append(card)

    # Filter by skill tag if specified
    if skill:
        cards = [
            card
            for card in cards
            if any(skill.lower() in tag.lower() for s in card.skills for tag in s.tags)
        ]

    return cards


@router.get("/agents/{agent_id}/card")
async def get_agent_card_by_id(
    agent_id: str,
    db: DbSession,
) -> AgentCard:
    """
    Get Agent Card for a specific agent by ID or slug.

    Alternative to the /.well-known/agent.json endpoint for programmatic access.
    """
    # Try to parse as UUID first
    try:
        uuid = UUID(agent_id)
        result = await db.execute(select(AgentTable).where(AgentTable.id == uuid))
    except ValueError:
        # Not a UUID, try slug lookup
        result = await db.execute(
            select(AgentTable).where(AgentTable.slug == agent_id)
        )

    agent = result.scalar_one_or_none()

    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id}",
        )

    return await _build_agent_card(agent)
