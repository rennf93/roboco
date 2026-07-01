"""Dev intent-verb HTTP endpoints. Thin handlers; delegate to Choreographer."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request

from roboco.api.deps import get_choreographer
from roboco.api.routes.v1._role_dep import envelope_to_response, require_dev
from roboco.api.schemas.v1.flow import (
    GiveMeWorkRequest,
    IAmBlockedRequest,
    IAmDoneRequest,
    IAmIdleRequest,
    IWillWorkOnRequest,
    OpenPrRequest,
    ResumeRequest,
    SyncBranchRequest,
    UnclaimRequest,
)
from roboco.security import guard_deco
from roboco.services.gateway.choreographer import Choreographer

router = APIRouter(
    prefix="/api/v1/flow/developer",
    tags=["v1-flow-developer"],
    dependencies=[require_dev],
)


_AgentIdHeader = Annotated[UUID, Header(alias="X-Agent-ID")]
_ChoreographerDep = Annotated[Choreographer, Depends(get_choreographer)]


@router.post("/give_me_work")
@guard_deco.rate_limit(requests=30, window=60)
async def give_me_work(
    request: Request,
    _body: GiveMeWorkRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.give_me_work(x_agent_id)
    return envelope_to_response(env, request)


@router.post("/i_will_work_on")
@guard_deco.rate_limit(requests=30, window=60)
async def i_will_work_on(
    request: Request,
    body: IWillWorkOnRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.i_will_work_on(
        x_agent_id,
        body.task_id,
        body.plan,
        steps=body.steps,
        technical_considerations=body.technical_considerations,
        risks=body.risks,
        open_questions=body.open_questions,
    )
    return envelope_to_response(env, request)


@router.post("/open_pr")
@guard_deco.rate_limit(requests=30, window=60)
async def open_pr(
    request: Request,
    body: OpenPrRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.open_pr(x_agent_id, body.task_id)
    return envelope_to_response(env, request)


@router.post("/i_am_done")
@guard_deco.rate_limit(requests=30, window=60)
async def i_am_done(
    request: Request,
    body: IAmDoneRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.i_am_done(x_agent_id, body.task_id, body.notes)
    return envelope_to_response(env, request)


@router.post("/i_am_blocked")
@guard_deco.rate_limit(requests=30, window=60)
async def i_am_blocked(
    request: Request,
    body: IAmBlockedRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.i_am_blocked(
        x_agent_id,
        body.task_id,
        body.reason,
        blocker_type=body.blocker_type,
        what_needed=body.what_needed,
    )
    return envelope_to_response(env, request)


@router.post("/unclaim")
@guard_deco.rate_limit(requests=30, window=60)
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
async def resume(
    request: Request,
    body: ResumeRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.resume(x_agent_id, body.task_id)
    return envelope_to_response(env, request)


@router.post("/sync_branch")
@guard_deco.rate_limit(requests=30, window=60)
async def sync_branch(
    request: Request,
    body: SyncBranchRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.sync_branch(x_agent_id, body.task_id)
    return envelope_to_response(env, request)


@router.post("/i_am_idle")
@guard_deco.rate_limit(requests=30, window=60)
async def i_am_idle(
    request: Request,
    _body: IAmIdleRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.i_am_idle(x_agent_id)
    return envelope_to_response(env, request)
