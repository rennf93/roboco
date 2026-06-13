"""Live intake chat — the panel <-> spawned-agent bridge.

Endpoints over the in-process ``PrompterLiveRegistry``:

- ``POST /live/start``                 — spawn the intake container for a scope.
- ``GET  /live/{session_id}/stream``   — SSE: the agent's live events to the panel.
- ``POST /live/{session_id}/messages`` — the human's message in (panel -> agent).
- ``POST /live/{session_id}/stop``     — reap the session (panel close / confirm).
- ``POST /live/{session_id}/events``   — the agent's events in (container -> relay).

Streaming/messaging require the session to be live (its ``prompter`` container
spawned, which calls ``registry.open``). Auth is intentionally light here —
sessions are keyed by an opaque id on a trusted network; token enforcement is
Phase 5.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field, model_validator
from sse_starlette import EventSourceResponse

from roboco.api.deps import (
    CurrentAgentContext,
    DbSession,
    get_orchestrator,
    require_pm_or_above,
)
from roboco.services.base import NotFoundError, ServiceError, ValidationError
from roboco.services.prompter import get_prompter_service
from roboco.services.prompter_live import get_live_registry

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

router = APIRouter()


def _translate_service_error(e: ServiceError) -> HTTPException:
    """Service error → HTTP status (mirrors the legacy prompter route)."""
    if isinstance(e, NotFoundError):
        return HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "not_found", "message": e.message},
        )
    if isinstance(e, ValidationError):
        return HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "validation_error",
                "message": e.message,
                "field": e.field,
            },
        )
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={"error": "internal_error", "message": e.message},
    )


class StartLiveRequest(BaseModel):
    """Open a live intake chat scoped to a project XOR a product."""

    project_id: UUID | None = None
    product_id: UUID | None = None
    initial_message: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def _exactly_one_scope(self) -> StartLiveRequest:
        if bool(self.project_id) == bool(self.product_id):
            raise ValueError("provide exactly one of project_id / product_id")
        return self


class StartLiveResponse(BaseModel):
    """The new session's id — the panel opens its stream and posts messages to it."""

    session_id: str


class LiveMessageRequest(BaseModel):
    """The human's message in an active intake chat."""

    text: str = Field(..., min_length=1)


class AgentEvent(BaseModel):
    """One normalized event the container relays (mirrors driver.StreamChunk)."""

    kind: str
    text: str = ""
    tool: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


@router.post(
    "/live/start",
    response_model=StartLiveResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_live(body: StartLiveRequest, db: DbSession) -> StartLiveResponse:
    """Spawn the intake agent for a new chat and return its session id.

    The panel then opens ``/live/{session_id}/stream`` and posts messages to
    ``/live/{session_id}/messages``. An ``initial_message`` is delivered to the
    agent automatically once its container is reachable.
    """
    project_slug: str | None = None
    if body.project_id is not None:
        from roboco.services.project import get_project_service

        project = await get_project_service(db).get(body.project_id)
        if project is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Project {body.project_id} not found",
            )
        project_slug = project.slug

    session_id = uuid4().hex
    try:
        # Non-blocking: opens the relay + spawns the container in the background,
        # so this request returns immediately (no 60s timeout on clone/build/run).
        await get_orchestrator().start_intake_session(
            session_id,
            project_slug=project_slug,
            product_id=str(body.product_id) if body.product_id else None,
            initial_message=body.initial_message,
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start intake session: {exc}",
        ) from exc
    return StartLiveResponse(session_id=session_id)


@router.get("/live/{session_id}/stream")
async def stream(session_id: str, request: Request) -> EventSourceResponse:
    """Stream the agent's live events (token deltas, tool calls) to the panel."""
    registry = get_live_registry()

    async def events() -> AsyncGenerator[dict[str, Any]]:
        async for event in registry.stream(session_id):
            if await request.is_disconnected():
                break
            yield {"event": event.get("kind", "message"), "data": json.dumps(event)}

    return EventSourceResponse(events(), ping=15)


@router.get("/live/{session_id}/status")
async def session_status(session_id: str) -> dict[str, bool]:
    """Report whether a live intake session is still running.

    The panel calls this after a reload: if the session survived (the agent
    container outlives a browser refresh), it reopens the SSE stream and
    resumes the chat instead of dropping back to the scope form.
    """
    return {"alive": get_live_registry().is_alive(session_id)}


@router.post("/live/{session_id}/messages")
async def send_message(session_id: str, body: LiveMessageRequest) -> dict[str, bool]:
    """Deliver the human's message to the running intake agent."""
    delivered = await get_live_registry().deliver(session_id, body.text)
    if not delivered:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "not_found",
                "message": f"No live intake session {session_id} (spawn it first).",
            },
        )
    return {"delivered": True}


@router.post("/live/{session_id}/stop")
async def stop_live(session_id: str) -> dict[str, bool]:
    """Reap the live intake session (panel close, or draft confirmed)."""
    await get_orchestrator().reap_intake_session(session_id)
    return {"stopped": True}


class LiveConfirmRequest(BaseModel):
    """Confirm the agent's draft → a task, scoped to exactly one target.

    ``route`` is which start button the human pressed: ``"board"`` (Board review
    & Start → PO + HoM review first) or ``"main_pm"`` (Approve & Start → straight
    to the Main PM).
    """

    project_id: UUID | None = None
    product_id: UUID | None = None
    draft: dict[str, Any]
    route: Literal["board", "main_pm"] = "board"
    # Set on a board-informed re-draft: confirm updates this existing task in
    # place instead of creating a new one. When present, project/product scope
    # is taken from the task, so neither is required here.
    task_id: UUID | None = None

    @model_validator(mode="after")
    def _exactly_one_target(self) -> LiveConfirmRequest:
        if self.task_id is not None:
            return self
        if bool(self.project_id) == bool(self.product_id):
            raise ValueError("provide exactly one of project_id / product_id")
        return self


@router.post("/live/{session_id}/confirm", status_code=status.HTTP_201_CREATED)
async def confirm_live(
    session_id: str,
    body: LiveConfirmRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> dict[str, str]:
    """Turn the agent's confirmed draft into a started (pending) task, then reap.

    Per ``route``: "Board review & Start" assigns it to the Board (PO + HoM
    review first); "Approve & Start" assigns it straight to the Main PM.
    Attributed to the confirming agent (the CEO). On success the live session is
    reaped — the chat is over once the draft is a task.
    """
    service = get_prompter_service(db)
    try:
        if body.task_id is not None:
            # Board-informed re-draft: update the existing task in place.
            task_id = await service.update_live_draft(
                body.task_id, body.draft, route=body.route
            )
        else:
            task_id = await service.confirm_live_draft(
                body.draft,
                agent.agent_id,
                project_id=body.project_id,
                product_id=body.product_id,
                route=body.route,
            )
    except ServiceError as e:
        raise _translate_service_error(e) from e
    await db.commit()

    # The draft is now a task — reap the agent + close the relay stream.
    await get_orchestrator().reap_intake_session(session_id)
    return {"task_id": str(task_id)}


async def _intake_scope_for_task(
    db: DbSession, task: Any
) -> tuple[str | None, str | None]:
    """Return (project_slug, product_id) intake scope for a task — exactly one."""
    if task.product_id is not None:
        return None, str(task.product_id)
    if task.project_id is not None:
        from roboco.services.project import get_project_service

        proj = await get_project_service(db).get(UUID(str(task.project_id)))
        return (proj.slug if proj else None), None
    return None, None


@router.post(
    "/live/re-interview/{task_id}",
    response_model=StartLiveResponse,
    status_code=status.HTTP_201_CREATED,
)
async def re_interview(
    task_id: UUID, db: DbSession, agent: CurrentAgentContext
) -> StartLiveResponse:
    """Re-open intake to re-draft a board-reviewed task with the board's feedback.

    Spawns a fresh intake session seeded with the current draft + the Product
    Owner / Head of Marketing review, scoped to the task's product/project. The
    panel opens its stream and, on confirm, passes ``task_id`` so the revised
    draft updates this task instead of creating a new one. This is the cold path
    (and the resilience fallback for the keep-alive re-draft).
    """
    from roboco.services.journal import get_journal_service
    from roboco.services.prompter import compose_redraft_message
    from roboco.services.task import get_task_service

    require_pm_or_above(agent.role, "re-interview a task")
    task = await get_task_service(db).get(task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Task {task_id} not found"
        )
    project_slug, product_id = await _intake_scope_for_task(db, task)
    if not project_slug and not product_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Task has no project/product scope to re-interview against.",
        )

    entries = await get_journal_service(db).board_review_brief(task_id)
    initial_message = compose_redraft_message(task, entries)

    session_id = uuid4().hex
    try:
        await get_orchestrator().start_intake_session(
            session_id,
            project_slug=project_slug,
            product_id=product_id,
            initial_message=initial_message,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start re-interview session: {exc}",
        ) from exc
    return StartLiveResponse(session_id=session_id)


@router.post("/live/{session_id}/events")
async def relay_event(session_id: str, event: AgentEvent) -> dict[str, bool]:
    """Relay one agent event from the container onto the session's stream."""
    return {"pushed": get_live_registry().push(session_id, event.model_dump())}
