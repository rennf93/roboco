"""Main PM intent-verb HTTP endpoints. Thin handlers; delegate to Choreographer."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header

from roboco.api.deps import get_choreographer
from roboco.api.routes.v2._role_dep import require_main_pm
from roboco.api.schemas.v2.flow import (
    CompleteRequest,
    DelegateRequest,
    EscalateToCeoRequest,
    EscalateUpRequest,
    GiveMeWorkRequest,
    IAmIdleRequest,
    IWillPlanRequest,
    ResumeRequest,
    TriageRequest,
    UnblockRequest,
    UnclaimRequest,
)
from roboco.services.gateway.choreographer import Choreographer, DelegateInputs

router = APIRouter(
    prefix="/api/v2/flow/main_pm",
    tags=["v2-flow-main-pm"],
    dependencies=[require_main_pm],
)


_AgentIdHeader = Annotated[UUID, Header(alias="X-Agent-ID")]
_ChoreographerDep = Annotated[Choreographer, Depends(get_choreographer)]


@router.post("/give_me_work")
async def give_me_work(
    _body: GiveMeWorkRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.pm_give_me_work(x_agent_id)
    return env.as_dict()


@router.post("/i_will_plan")
async def i_will_plan(
    body: IWillPlanRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.i_will_plan(x_agent_id, body.task_id, body.plan)
    return env.as_dict()


@router.post("/delegate")
async def delegate(
    body: DelegateRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    inputs = DelegateInputs(
        title=body.title,
        description=body.description,
        assigned_to=body.assigned_to,
        team=body.team,
        task_type=body.task_type,
        acceptance_criteria=body.acceptance_criteria,
        estimated_complexity=body.estimated_complexity,
    )
    env = await choreographer.delegate(x_agent_id, body.parent_task_id, inputs)
    return env.as_dict()


@router.post("/triage_all")
async def triage_all(
    _body: TriageRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.triage_all(x_agent_id)
    return env.as_dict()


@router.post("/complete")
async def complete(
    body: CompleteRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.main_pm_complete(x_agent_id, body.task_id, body.notes)
    return env.as_dict()


@router.post("/escalate_up")
async def escalate_up(
    body: EscalateUpRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.escalate_up(x_agent_id, body.task_id, body.reason)
    return env.as_dict()


@router.post("/escalate_to_ceo")
async def escalate_to_ceo(
    body: EscalateToCeoRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.escalate_to_ceo(x_agent_id, body.task_id, body.reason)
    return env.as_dict()


@router.post("/unblock")
async def unblock(
    body: UnblockRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.unblock(x_agent_id, body.task_id, restore=body.restore)
    return env.as_dict()


@router.post("/unclaim")
async def unclaim(
    body: UnclaimRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.unclaim(x_agent_id, body.task_id)
    return env.as_dict()


@router.post("/resume")
async def resume(
    body: ResumeRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.resume(x_agent_id, body.task_id)
    return env.as_dict()


@router.post("/i_am_idle")
async def i_am_idle(
    _body: IAmIdleRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.i_am_idle(x_agent_id)
    return env.as_dict()
