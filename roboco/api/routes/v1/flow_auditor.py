"""Auditor intent-verb HTTP endpoints. Read-only.

Thin handlers; delegate to Choreographer.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from guard_core.handlers.behavior_handler import BehaviorRule

from roboco.api.deps import get_choreographer
from roboco.api.routes.v1._role_dep import envelope_to_response, require_auditor
from roboco.api.schemas.v1.flow import (
    IAmIdleRequest,
    TriageRequest,
    WaiveFindingRequest,
)
from roboco.security import guard_deco
from roboco.services.gateway.choreographer import Choreographer

_RUNAWAY_RULES = [
    BehaviorRule(rule_type="frequency", threshold=120, window=60, action="log")
]

router = APIRouter(
    prefix="/api/v1/flow/auditor",
    tags=["v1-flow-auditor"],
    dependencies=[require_auditor],
)


_AgentIdHeader = Annotated[UUID, Header(alias="X-Agent-ID")]
_ChoreographerDep = Annotated[Choreographer, Depends(get_choreographer)]


@router.post("/triage")
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def triage(
    request: Request,
    _body: TriageRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.auditor_triage(x_agent_id)
    return envelope_to_response(env, request)


@router.post("/waive_finding")
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def waive_finding(
    request: Request,
    body: WaiveFindingRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.waive_finding(x_agent_id, body.finding_id, body.note)
    return envelope_to_response(env, request)


@router.post("/i_am_idle")
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def i_am_idle(
    request: Request,
    _body: IAmIdleRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.i_am_idle(x_agent_id)
    return envelope_to_response(env, request)
