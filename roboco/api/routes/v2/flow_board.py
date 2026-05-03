"""Board (PO + Head Marketing) intent-verb HTTP endpoints.

Thin handlers; delegate to Choreographer.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request

from roboco.api.deps import get_choreographer
from roboco.api.routes.v2._role_dep import envelope_to_response, require_board
from roboco.api.schemas.v2.flow import (
    EscalateToCeoRequest,
    IAmIdleRequest,
    TriageRequest,
)
from roboco.services.gateway.choreographer import Choreographer

router = APIRouter(
    prefix="/api/v2/flow/board",
    tags=["v2-flow-board"],
    dependencies=[require_board],
)


_AgentIdHeader = Annotated[UUID, Header(alias="X-Agent-ID")]
_ChoreographerDep = Annotated[Choreographer, Depends(get_choreographer)]


@router.post("/triage")
async def triage(
    request: Request,
    _body: TriageRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.board_triage(x_agent_id)
    return envelope_to_response(env, request)


@router.post("/escalate_to_ceo")
async def escalate_to_ceo(
    request: Request,
    body: EscalateToCeoRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.escalate_to_ceo(x_agent_id, body.task_id, body.reason)
    return envelope_to_response(env, request)


@router.post("/i_am_idle")
async def i_am_idle(
    request: Request,
    _body: IAmIdleRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.i_am_idle(x_agent_id)
    return envelope_to_response(env, request)
