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
from typing import TYPE_CHECKING, Annotated, Any
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sse_starlette import EventSourceResponse

from roboco.api.deps import (
    CurrentAgentContext,
    DbSession,
    get_orchestrator,
    require_panel_token,
    require_pm_or_above,
)
from roboco.api.schemas.prompter_live import (
    AgentEvent,
    BatchConfirmRequest,
    BatchPreviewRequest,
    LiveConfirmRequest,
    LiveMessageRequest,
    StartLiveRequest,
    StartLiveResponse,
)
from roboco.security import guard_deco, prompt_injection_validator
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


@router.post(
    "/live/start",
    response_model=StartLiveResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_panel_token)],
)
@guard_deco.rate_limit(requests=10, window=60)
@guard_deco.max_request_size(size_bytes=32768)
@guard_deco.custom_validation(prompt_injection_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
@guard_deco.suspicious_detection(enabled=True)
@guard_deco.block_clouds()
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

    project_ids = [str(pid) for pid in body.project_ids] if body.project_ids else None

    session_id = uuid4().hex
    try:
        # Non-blocking: opens the relay + spawns the container in the background,
        # so this request returns immediately (no 60s timeout on clone/build/run).
        await get_orchestrator().start_intake_session(
            session_id,
            project_slug=project_slug,
            product_id=str(body.product_id) if body.product_id else None,
            project_ids=project_ids,
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


@router.get("/live/{session_id}/stream", dependencies=[Depends(require_panel_token)])
async def stream(session_id: str, request: Request) -> EventSourceResponse:
    """Stream the agent's live events (token deltas, tool calls) to the panel."""
    registry = get_live_registry()

    async def events() -> AsyncGenerator[dict[str, Any]]:
        async for event in registry.stream(session_id):
            if await request.is_disconnected():
                break
            yield {"event": event.get("kind", "message"), "data": json.dumps(event)}

    return EventSourceResponse(events(), ping=15)


@router.get("/live/{session_id}/status", dependencies=[Depends(require_panel_token)])
async def session_status(session_id: str) -> dict[str, bool]:
    """Report whether a live intake session is still running.

    The panel calls this after a reload: if the session survived (the agent
    container outlives a browser refresh), it reopens the SSE stream and
    resumes the chat instead of dropping back to the scope form.
    """
    return {"alive": get_live_registry().is_alive(session_id)}


@router.post("/live/{session_id}/messages", dependencies=[Depends(require_panel_token)])
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.max_request_size(size_bytes=32768)
@guard_deco.custom_validation(prompt_injection_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
@guard_deco.suspicious_detection(enabled=True)
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


@router.post("/live/{session_id}/stop", dependencies=[Depends(require_panel_token)])
async def stop_live(session_id: str) -> dict[str, bool]:
    """Reap the live intake session (panel close, or draft confirmed)."""
    await get_orchestrator().reap_intake_session(session_id)
    return {"stopped": True}


@router.post("/live/{session_id}/confirm", status_code=status.HTTP_201_CREATED)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
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

    # Board route (first confirm, not a re-draft): keep the intake agent alive
    # and PARK it against this task, so when the board finishes its review the
    # orchestrator can inject that feedback in-context for an in-place re-draft
    # (the agent still holds the whole interview). Every other path is terminal
    # → reap. If parking fails (session already gone), fall through to reap so
    # nothing leaks.
    if (
        body.task_id is None
        and body.route == "board"
        and get_live_registry().park(session_id, str(task_id))
    ):
        return {"task_id": str(task_id)}

    # The draft is now a task — reap the agent + close the relay stream.
    await get_orchestrator().reap_intake_session(session_id)
    return {"task_id": str(task_id)}


@router.post(
    "/live/{session_id}/preview-batch", dependencies=[Depends(require_panel_token)]
)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
async def preview_live_batch(
    session_id: str,  # noqa: ARG001 — kept for route symmetry; preview is pure
    body: BatchPreviewRequest,
    db: DbSession,
) -> dict[str, Any]:
    """Compute a MegaTask's waves from the proposed drafts WITHOUT creating it.

    Lets the panel show the human the conflict-free wave plan before they confirm
    the batch. Pure compute — no task is created and the live session is left
    running so the human can still keep chatting.
    """
    service = get_prompter_service(db)
    try:
        return service.preview_batch(body.drafts)
    except ServiceError as e:
        raise _translate_service_error(e) from e


@router.post("/live/{session_id}/confirm-batch", status_code=status.HTTP_201_CREATED)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
async def confirm_live_batch(
    session_id: str,
    body: BatchConfirmRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> dict[str, Any]:
    """Turn the agent's confirmed MegaTask (N drafts) into a sequenced batch.

    Builds the branchless umbrella + one root-subtask per draft, wires the
    collision-derived dependency waves, and routes the umbrella per ``route``
    (Board review vs. straight to the Main PM) — each root-subtask keeps its own
    project / branch / PR. Returns the umbrella id, the root-subtask ids, and the
    computed waves + warnings for the panel.

    ``task_id`` set is a board-informed re-draft: updates the existing MegaTask
    umbrella + root-subtasks in place instead of creating a new batch (mirrors
    ``confirm_live``'s single-task re-draft), and always reaps. On a first
    confirm (``task_id`` None), the "Board review & Start" route parks the
    intake agent against the umbrella instead of reaping — same keep-alive
    re-draft loop as a single-task confirm — so the board's feedback can be
    injected in-context; every other path reaps once the drafts are tasks.
    """
    service = get_prompter_service(db)
    try:
        if body.task_id is not None:
            result = await service.update_live_batch(
                body.task_id,
                body.title,
                body.drafts,
                agent.agent_id,
                project_ids=body.project_ids,
                route=body.route,
                agent_role=str(agent.role.value),
            )
        else:
            result = await service.confirm_live_batch(
                body.title,
                body.drafts,
                agent.agent_id,
                project_ids=body.project_ids or [],
                route=body.route,
                session_id=session_id,
            )
    except ServiceError as e:
        raise _translate_service_error(e) from e
    await db.commit()

    if (
        body.task_id is None
        and body.route == "board"
        and get_live_registry().park(session_id, result["umbrella_task_id"])
    ):
        return result

    await get_orchestrator().reap_intake_session(session_id)
    return result


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


async def _start_batch_re_interview(
    db: DbSession, umbrella: Any, entries: list[dict[str, Any]]
) -> StartLiveResponse:
    """Cold re-interview for a MegaTask umbrella.

    Recovers the batch's multi-repo scope from its root-subtasks' own project /
    cell-map targets (no single project/product lives on the branchless
    umbrella) and seeds a batch-aware redraft message. 400 only when nothing is
    recoverable (e.g. every root-subtask was itself cancelled).
    """
    from roboco.services.prompter import compose_batch_redraft_message
    from roboco.services.task import get_task_service

    task_service = get_task_service(db)
    umbrella_id = UUID(str(umbrella.id))
    children = await task_service.get_live_subtasks(umbrella_id)
    project_ids = await task_service.distinct_projects_for_batch(umbrella_id)
    if not project_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This MegaTask has no recoverable projects to re-interview against.",
        )
    initial_message = compose_batch_redraft_message(umbrella, children, entries)

    session_id = uuid4().hex
    try:
        await get_orchestrator().start_intake_session(
            session_id,
            project_ids=[str(pid) for pid in project_ids],
            initial_message=initial_message,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to start re-interview session: {exc}",
        ) from exc
    return StartLiveResponse(session_id=session_id, project_ids=project_ids)


@router.post(
    "/live/re-interview/{task_id}",
    response_model=StartLiveResponse,
    status_code=status.HTTP_201_CREATED,
)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
async def re_interview(
    task_id: UUID, db: DbSession, agent: CurrentAgentContext
) -> StartLiveResponse:
    """Re-open intake to re-draft a board-reviewed task with the board's feedback.

    Spawns a fresh intake session seeded with the current draft + the Product
    Owner / Head of Marketing review, scoped to the task's product/project. The
    panel opens its stream and, on confirm, passes ``task_id`` so the revised
    draft updates this task instead of creating a new one. This is the cold path
    (and the resilience fallback for the keep-alive re-draft).

    A MegaTask umbrella takes a separate branch (``_start_batch_re_interview``):
    it carries no project/product of its own, so its scope is recovered from
    its root-subtasks instead.
    """
    from roboco.foundation.policy.batch import is_batch_umbrella
    from roboco.services.journal import get_journal_service
    from roboco.services.prompter import compose_redraft_message
    from roboco.services.task import get_task_service

    require_pm_or_above(agent.role, "re-interview a task")
    task = await get_task_service(db).get(task_id)
    if task is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Task {task_id} not found"
        )
    entries = await get_journal_service(db).board_review_brief(task_id)

    if is_batch_umbrella(batch_id=task.batch_id, parent_task_id=task.parent_task_id):
        return await _start_batch_re_interview(db, task, entries)

    project_slug, product_id = await _intake_scope_for_task(db, task)
    if not project_slug and not product_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Task has no project/product scope to re-interview against.",
        )
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
@guard_deco.rate_limit(requests=60, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(prompt_injection_validator)
@guard_deco.content_type_filter(["application/json"])
async def relay_event(session_id: str, event: AgentEvent) -> dict[str, bool]:
    """Relay one agent event from the container onto the session's stream."""
    return {"pushed": get_live_registry().push(session_id, event.model_dump())}


_SEARCH_TASKS_DEFAULT_LIMIT = 8
_SEARCH_TASKS_MAX_LIMIT = 10


@router.get("/live/{session_id}/search-tasks")
async def search_past_tasks(
    session_id: str,
    db: DbSession,
    q: Annotated[str, Query(min_length=2, max_length=200)],
    limit: Annotated[int, Query(ge=1, le=_SEARCH_TASKS_MAX_LIMIT)] = (
        _SEARCH_TASKS_DEFAULT_LIMIT
    ),
) -> list[dict[str, Any]]:
    """Bounded compact task search for the intake agent's ``search_past_tasks``
    tool — "have we done something like this before?" mid-conversation.

    Session-scoped as a trust boundary (the intake container has no agent
    identity, matching ``/events``): a dead/unknown session is rejected so
    only a live intake container can query.
    """
    if not get_live_registry().is_alive(session_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No live intake session {session_id}",
        )
    from roboco.services.prompter import compact_task_rows
    from roboco.services.task import get_task_service

    rows = await get_task_service(db).search_tasks(q, limit=limit)
    return compact_task_rows(rows)
