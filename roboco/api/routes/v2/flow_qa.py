"""QA intent-verb HTTP endpoints. Thin handlers; delegate to Choreographer."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header

from roboco.api.deps import get_choreographer
from roboco.api.routes.v2._role_dep import require_qa
from roboco.api.schemas.v2.flow import (
    ClaimReviewRequest,
    FailReviewRequest,
    GiveMeWorkRequest,
    IAmIdleRequest,
    PassReviewRequest,
    UnclaimRequest,
)
from roboco.services.gateway.choreographer import Choreographer

router = APIRouter(
    prefix="/api/v2/flow/qa",
    tags=["v2-flow-qa"],
    dependencies=[require_qa],
)


_AgentIdHeader = Annotated[UUID, Header(alias="X-Agent-ID")]
_ChoreographerDep = Annotated[Choreographer, Depends(get_choreographer)]


@router.post("/give_me_work")
async def give_me_work(
    _body: GiveMeWorkRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.give_me_work(x_agent_id)
    return env.as_dict()


@router.post("/claim_review")
async def claim_review(
    body: ClaimReviewRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.claim_review(x_agent_id, body.task_id)
    return env.as_dict()


@router.post("/pass")
async def qa_pass(
    body: PassReviewRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.pass_review(x_agent_id, body.task_id, body.notes)
    return env.as_dict()


@router.post("/fail")
async def qa_fail(
    body: FailReviewRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.fail_review(x_agent_id, body.task_id, body.issues)
    return env.as_dict()


@router.post("/unclaim")
async def unclaim(
    body: UnclaimRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.unclaim(x_agent_id, body.task_id)
    return env.as_dict()


@router.post("/i_am_idle")
async def i_am_idle(
    _body: IAmIdleRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.i_am_idle(x_agent_id)
    return env.as_dict()
