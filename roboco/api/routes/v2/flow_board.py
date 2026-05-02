"""Board (PO + Head Marketing) intent-verb HTTP endpoints.

Thin handlers; delegate to Choreographer.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header

from roboco.api.deps import get_choreographer
from roboco.api.schemas.v2.flow import (
    EscalateToCeoRequest,
    IAmIdleRequest,
    TriageRequest,
)
from roboco.services.gateway.choreographer import Choreographer

router = APIRouter(prefix="/api/v2/flow/board", tags=["v2-flow-board"])


_AgentIdHeader = Annotated[UUID, Header(alias="X-Agent-ID")]
_ChoreographerDep = Annotated[Choreographer, Depends(get_choreographer)]


@router.post("/triage")
async def triage(
    _body: TriageRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.board_triage(x_agent_id)
    return env.as_dict()


@router.post("/escalate_to_ceo")
async def escalate_to_ceo(
    body: EscalateToCeoRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.escalate_to_ceo(x_agent_id, body.task_id, body.reason)
    return env.as_dict()


@router.post("/i_am_idle")
async def i_am_idle(
    _body: IAmIdleRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.i_am_idle(x_agent_id)
    return env.as_dict()
