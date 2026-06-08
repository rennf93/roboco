"""Live intake chat — the panel <-> spawned-agent bridge.

Three endpoints over the in-process ``PrompterLiveRegistry``:

- ``GET  /live/{session_id}/stream``   — SSE: the agent's live events to the panel.
- ``POST /live/{session_id}/messages`` — the human's message in (panel -> agent).
- ``POST /live/{session_id}/events``   — the agent's events in (container -> relay).

The session must already be live (its ``prompter`` container spawned, which
calls ``registry.open``). Auth is intentionally light here — these are keyed by
the opaque session id on a trusted network; token enforcement is Phase 5.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field
from sse_starlette import EventSourceResponse

from roboco.services.prompter_live import get_live_registry

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

router = APIRouter()


class LiveMessageRequest(BaseModel):
    """The human's message in an active intake chat."""

    text: str = Field(..., min_length=1)


class AgentEvent(BaseModel):
    """One normalized event the container relays (mirrors driver.StreamChunk)."""

    kind: str
    text: str = ""
    tool: str = ""
    data: dict[str, Any] = Field(default_factory=dict)


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


@router.post("/live/{session_id}/events")
async def relay_event(session_id: str, event: AgentEvent) -> dict[str, bool]:
    """Relay one agent event from the container onto the session's stream."""
    return {"pushed": get_live_registry().push(session_id, event.model_dump())}
