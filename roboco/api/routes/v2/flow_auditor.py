"""Auditor intent-verb HTTP endpoints. Read-only.

Thin handlers; delegate to Choreographer.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header

from roboco.api.deps import get_choreographer
from roboco.api.routes.v2._role_dep import require_auditor
from roboco.api.schemas.v2.flow import IAmIdleRequest, TriageRequest
from roboco.services.gateway.choreographer import Choreographer

router = APIRouter(
    prefix="/api/v2/flow/auditor",
    tags=["v2-flow-auditor"],
    dependencies=[require_auditor],
)


_AgentIdHeader = Annotated[UUID, Header(alias="X-Agent-ID")]
_ChoreographerDep = Annotated[Choreographer, Depends(get_choreographer)]


@router.post("/triage")
async def triage(
    _body: TriageRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.auditor_triage(x_agent_id)
    return env.as_dict()


@router.post("/i_am_idle")
async def i_am_idle(
    _body: IAmIdleRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.i_am_idle(x_agent_id)
    return env.as_dict()
