"""Live Secretary chat — the panel <-> Secretary container bridge.

Mirrors the intake live bridge over the shared ``PrompterLiveRegistry``, scoped
to the Secretary session (no project/product). Auth is intentionally light here
(opaque session id on a trusted network); the Secretary's *authority* is gated
at ``/api/secretary/directives``.

- ``POST /live/start``                 — spawn the Secretary container.
- ``GET  /live/{session_id}/stream``   — SSE: the agent's live events to the panel.
- ``GET  /live/{session_id}/status``   — is the session still alive?
- ``POST /live/{session_id}/messages`` — the CEO's message in (panel -> agent).
- ``POST /live/{session_id}/stop``     — reap the session.
- ``POST /live/{session_id}/events``   — the agent's events in (container -> relay).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sse_starlette import EventSourceResponse

from roboco.api.deps import get_orchestrator
from roboco.services.prompter_live import get_live_registry

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

router = APIRouter()


class StartSecretaryRequest(BaseModel):
    """Open a live Secretary chat (optionally with an opening message)."""

    initial_message: str | None = Field(default=None, min_length=1)


class StartSecretaryResponse(BaseModel):
    session_id: str


class LiveMessageRequest(BaseModel):
    text: str = Field(..., min_length=1)


class AgentEvent(BaseModel):
    """One event relayed from the container onto the session stream."""

    kind: str
    text: str = ""
    tool: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


@router.post(
    "/live/start",
    response_model=StartSecretaryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def start_live(body: StartSecretaryRequest) -> StartSecretaryResponse:
    """Spawn the Secretary agent for a new chat and return its session id."""
    session_id = uuid4().hex
    try:
        await get_orchestrator().start_secretary_session(
            session_id, initial_message=body.initial_message
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"error": "spawn_failed", "message": str(exc)},
        ) from exc
    return StartSecretaryResponse(session_id=session_id)


@router.get("/live/{session_id}/stream")
async def stream(session_id: str, request: Request) -> EventSourceResponse:
    """Stream the Secretary's live events (token deltas, tool calls) to the panel."""
    registry = get_live_registry()

    async def events() -> AsyncGenerator[dict[str, Any]]:
        async for event in registry.stream(session_id):
            if await request.is_disconnected():
                break
            yield {"event": event.get("kind", "message"), "data": json.dumps(event)}

    return EventSourceResponse(events(), ping=15)


@router.get("/live/{session_id}/status")
async def session_status(session_id: str) -> dict[str, bool]:
    """Report whether a live Secretary session is still running."""
    return {"alive": get_live_registry().is_alive(session_id)}


@router.post("/live/{session_id}/messages")
async def send_message(session_id: str, body: LiveMessageRequest) -> dict[str, bool]:
    """Deliver the CEO's message to the running Secretary agent."""
    delivered = await get_live_registry().deliver(session_id, body.text)
    if not delivered:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "not_found",
                "message": f"No live secretary session {session_id} (start it first).",
            },
        )
    return {"delivered": True}


@router.post("/live/{session_id}/stop")
async def stop_live(session_id: str) -> dict[str, bool]:
    """Reap the live Secretary session."""
    await get_orchestrator().reap_secretary_session(session_id)
    return {"stopped": True}


@router.post("/live/{session_id}/events")
async def relay_event(session_id: str, event: AgentEvent) -> dict[str, bool]:
    """Relay one agent event from the container onto the session's stream."""
    return {"pushed": get_live_registry().push(session_id, event.model_dump())}
