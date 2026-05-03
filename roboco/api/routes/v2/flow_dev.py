"""Dev intent-verb HTTP endpoints. Thin handlers; delegate to Choreographer."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header

from roboco.api.deps import get_choreographer
from roboco.api.routes.v2._role_dep import require_dev
from roboco.api.schemas.v2.flow import (
    GiveMeWorkRequest,
    IAmBlockedRequest,
    IAmDoneRequest,
    IAmIdleRequest,
    IHaveCommittedRequest,
    IWillWorkOnRequest,
    SubmitForQaRequest,
    UnclaimRequest,
)
from roboco.services.gateway.choreographer import Choreographer

router = APIRouter(
    prefix="/api/v2/flow/dev",
    tags=["v2-flow-dev"],
    dependencies=[require_dev],
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


@router.post("/i_will_work_on")
async def i_will_work_on(
    body: IWillWorkOnRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.i_will_work_on(x_agent_id, body.task_id, body.plan)
    return env.as_dict()


@router.post("/i_have_committed")
async def i_have_committed(
    body: IHaveCommittedRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.i_have_committed(x_agent_id, body.message)
    return env.as_dict()


@router.post("/submit_for_qa")
async def submit_for_qa(
    body: SubmitForQaRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.submit_for_qa(x_agent_id, body.task_id)
    return env.as_dict()


@router.post("/i_am_done")
async def i_am_done(
    body: IAmDoneRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.i_am_done(x_agent_id, body.task_id, body.notes)
    return env.as_dict()


@router.post("/i_am_blocked")
async def i_am_blocked(
    body: IAmBlockedRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.i_am_blocked(x_agent_id, body.task_id, body.reason)
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
