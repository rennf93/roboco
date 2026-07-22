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

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sse_starlette import EventSourceResponse

from roboco.api.deps import get_orchestrator, require_panel_token
from roboco.api.schemas.secretary_live import (
    AgentEvent,
    LiveMessageRequest,
    StartSecretaryRequest,
    StartSecretaryResponse,
)
from roboco.security import (
    guard_deco,
    prompt_injection_validator,
    secret_exfil_validator,
)
from roboco.services.prompter_live import get_live_registry

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

router = APIRouter()


@router.post(
    "/live/start",
    response_model=StartSecretaryResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_panel_token)],
)
@guard_deco.rate_limit(requests=10, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(prompt_injection_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
@guard_deco.suspicious_detection(enabled=True)
@guard_deco.block_clouds()
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


@router.get("/live/{session_id}/stream", dependencies=[Depends(require_panel_token)])
async def stream(session_id: str, request: Request) -> EventSourceResponse:
    """Stream the Secretary's live events (token deltas, tool calls) to the panel."""
    registry = get_live_registry()

    async def events() -> AsyncGenerator[dict[str, Any]]:
        async for event in registry.stream(session_id):
            if await request.is_disconnected():
                break
            yield {"event": event.get("kind", "message"), "data": json.dumps(event)}

    return EventSourceResponse(events(), ping=15)


@router.get("/live/{session_id}/status", dependencies=[Depends(require_panel_token)])
async def session_status(session_id: str) -> dict[str, bool]:
    """Report whether a live Secretary session is still running."""
    return {"alive": get_live_registry().is_alive(session_id)}


# Mirrors the fixed constant the orchestrator opens every Secretary session
# under (`SECRETARY_AGENT_ID` in roboco/runtime/orchestrator.py — the
# Secretary is a single seeded, persistent singleton, one container at a
# time). Duplicated as a literal rather than imported to keep this route
# decoupled from the orchestrator module.
_SECRETARY_AGENT_ID = "secretary-1"


@router.get("/live/active", dependencies=[Depends(require_panel_token)])
async def is_active() -> dict[str, bool]:
    """Is a Secretary live session running right now, under ANY session id?

    Read-only, derived from the registry alone — lets a device with no
    session id of its own (e.g. a fresh phone chat) tell "someone else is
    already chatting with the Secretary" apart from "nothing is running"
    before deciding whether to spawn a competing session against the same
    one-container singleton.
    """
    return {"active": get_live_registry().has_live_agent(_SECRETARY_AGENT_ID)}


@router.post("/live/{session_id}/messages", dependencies=[Depends(require_panel_token)])
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(prompt_injection_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.honeypot_detection(["email", "phone", "website"])
@guard_deco.suspicious_detection(enabled=True)
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


@router.post("/live/{session_id}/stop", dependencies=[Depends(require_panel_token)])
@guard_deco.rate_limit(requests=10, window=60)
async def stop_live(session_id: str) -> dict[str, bool]:
    """Reap the live Secretary session."""
    await get_orchestrator().reap_secretary_session(session_id)
    return {"stopped": True}


@router.post("/live/{session_id}/events")
@guard_deco.rate_limit(requests=60, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(secret_exfil_validator)
@guard_deco.content_type_filter(["application/json"])
async def relay_event(session_id: str, event: AgentEvent) -> dict[str, bool]:
    """Relay one agent event from the container onto the session's stream."""
    return {"pushed": get_live_registry().push(session_id, event.model_dump())}
