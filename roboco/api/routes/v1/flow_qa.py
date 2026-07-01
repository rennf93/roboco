"""QA intent-verb HTTP endpoints. Thin handlers; delegate to Choreographer."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from guard_core.handlers.behavior_handler import BehaviorRule

from roboco.api.deps import get_choreographer
from roboco.api.routes.v1._role_dep import envelope_to_response, require_qa
from roboco.api.schemas.v1.flow import (
    ClaimReviewRequest,
    FailReviewRequest,
    GiveMeWorkRequest,
    IAmBlockedRequest,
    IAmIdleRequest,
    PassReviewRequest,
    ResumeRequest,
    UnclaimRequest,
)
from roboco.security import guard_deco
from roboco.services.gateway.choreographer import Choreographer

_RUNAWAY_RULES = [
    BehaviorRule(rule_type="frequency", threshold=120, window=60, action="log")
]

router = APIRouter(
    prefix="/api/v1/flow/qa",
    tags=["v1-flow-qa"],
    dependencies=[require_qa],
)


_AgentIdHeader = Annotated[UUID, Header(alias="X-Agent-ID")]
_ChoreographerDep = Annotated[Choreographer, Depends(get_choreographer)]


@router.post("/give_me_work")
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def give_me_work(
    request: Request,
    _body: GiveMeWorkRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.give_me_work(x_agent_id)
    return envelope_to_response(env, request)


@router.post("/claim_review")
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def claim_review(
    request: Request,
    body: ClaimReviewRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.claim_review(x_agent_id, body.task_id)
    return envelope_to_response(env, request)


@router.post("/pass")
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def qa_pass(
    request: Request,
    body: PassReviewRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.pass_review(
        x_agent_id, body.task_id, body.notes, body.ac_verdicts
    )
    return envelope_to_response(env, request)


@router.post("/fail")
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def qa_fail(
    request: Request,
    body: FailReviewRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.fail_review(x_agent_id, body.task_id, body.issues)
    return envelope_to_response(env, request)


@router.post("/unclaim")
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def unclaim(
    request: Request,
    body: UnclaimRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.unclaim(x_agent_id, body.task_id)
    return envelope_to_response(env, request)


@router.post("/resume")
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def resume(
    request: Request,
    body: ResumeRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.resume(x_agent_id, body.task_id)
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


@router.post("/i_am_blocked")
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def i_am_blocked(
    request: Request,
    body: IAmBlockedRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    """Surface the ``i_am_blocked`` route so a blocked QA agent's escape hatch
    returns an envelope instead of a 404."""
    env = await choreographer.i_am_blocked(
        x_agent_id,
        body.task_id,
        body.reason,
        blocker_type=body.blocker_type,
        what_needed=body.what_needed,
    )
    return envelope_to_response(env, request)
