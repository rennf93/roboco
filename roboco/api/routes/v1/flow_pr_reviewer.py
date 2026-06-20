"""PR-reviewer intent-verb HTTP endpoints. Thin handlers; delegate to Choreographer."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request

from roboco.api.deps import get_choreographer
from roboco.api.routes.v1._role_dep import envelope_to_response, require_pr_reviewer
from roboco.api.schemas.v1.flow import (
    ClaimGateReviewRequest,
    ClaimPrReviewRequest,
    GiveMeWorkRequest,
    IAmIdleRequest,
    PostPrReviewRequest,
    PrFailRequest,
    PrPassRequest,
)
from roboco.services.gateway.choreographer import Choreographer

router = APIRouter(
    prefix="/api/v1/flow/pr_reviewer",
    tags=["v1-flow-pr-reviewer"],
    dependencies=[require_pr_reviewer],
)


_AgentIdHeader = Annotated[UUID, Header(alias="X-Agent-ID")]
_ChoreographerDep = Annotated[Choreographer, Depends(get_choreographer)]


@router.post("/give_me_work")
async def give_me_work(
    request: Request,
    _body: GiveMeWorkRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.give_me_work(x_agent_id)
    return envelope_to_response(env, request)


@router.post("/claim_pr_review")
async def claim_pr_review(
    request: Request,
    body: ClaimPrReviewRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.claim_pr_review(x_agent_id, body.task_id)
    return envelope_to_response(env, request)


@router.post("/post_pr_review")
async def post_pr_review(
    request: Request,
    body: PostPrReviewRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.post_pr_review(
        x_agent_id, body.task_id, body.body, body.event
    )
    return envelope_to_response(env, request)


@router.post("/claim_gate_review")
async def claim_gate_review(
    request: Request,
    body: ClaimGateReviewRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.claim_gate_review(x_agent_id, body.task_id)
    return envelope_to_response(env, request)


@router.post("/pr_pass")
async def pr_pass(
    request: Request,
    body: PrPassRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.pr_pass(x_agent_id, body.task_id, body.notes)
    return envelope_to_response(env, request)


@router.post("/pr_fail")
async def pr_fail(
    request: Request,
    body: PrFailRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.pr_fail(x_agent_id, body.task_id, body.issues)
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
