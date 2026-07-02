"""Main PM intent-verb HTTP endpoints. Thin handlers; delegate to Choreographer."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from guard_core.handlers.behavior_handler import BehaviorRule

from roboco.api.deps import get_choreographer
from roboco.api.routes.v1._role_dep import envelope_to_response, require_main_pm
from roboco.api.schemas.v1.flow import (
    CompleteRequest,
    DelegateRequest,
    EscalateToCeoRequest,
    EscalateUpRequest,
    GiveMeWorkRequest,
    IAmIdleRequest,
    IWillPlanRequest,
    RequestChangesRequest,
    ResumeRequest,
    SubmitRootRequest,
    TriageRequest,
    UnblockRequest,
    UnclaimRequest,
)
from roboco.security import guard_deco
from roboco.services.gateway.choreographer import Choreographer, DelegateInputs

_RUNAWAY_RULES = [
    BehaviorRule(rule_type="frequency", threshold=120, window=60, action="log")
]

router = APIRouter(
    prefix="/api/v1/flow/main_pm",
    tags=["v1-flow-main-pm"],
    dependencies=[require_main_pm],
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
    env = await choreographer.pm_give_me_work(x_agent_id)
    return envelope_to_response(env, request)


@router.post("/i_will_plan")
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def i_will_plan(
    request: Request,
    body: IWillPlanRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.i_will_plan(
        x_agent_id,
        body.task_id,
        body.plan,
        rich_plan={
            "approach": body.approach,
            "sub_tasks": body.sub_tasks,
            "technical_considerations": body.technical_considerations,
            "risks": body.risks,
            "open_questions": body.open_questions,
        },
    )
    return envelope_to_response(env, request)


@router.post("/delegate")
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def delegate(
    request: Request,
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
        nature=body.nature,
        acceptance_criteria=body.acceptance_criteria,
        estimated_complexity=body.estimated_complexity,
        project_id=body.project_id,
        covers_parent_criteria=body.covers_parent_criteria,
        intends_to_touch=body.intends_to_touch,
        adds_migration=body.adds_migration,
        touches_shared=body.touches_shared,
        depends_on=body.depends_on,
    )
    env = await choreographer.delegate(x_agent_id, body.parent_task_id, inputs)
    return envelope_to_response(env, request)


@router.post("/triage_all")
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def triage_all(
    request: Request,
    _body: TriageRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.triage_all(x_agent_id)
    return envelope_to_response(env, request)


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
    """Team-scoped triage for the Main PM (own-team blocked / awaiting_pm_review).

    F067: the main_pm manifest advertises ``triage`` (lifecycle.intents_for_role
    includes it for MAIN_PM via _PM_ROLES) alongside ``triage_all``; without a
    route a main_pm agent calling `triage` hit a raw 404 that bypassed the
    per-verb circuit breaker. ``choreographer.triage`` is team-scoped (uses
    ``pm.team``) and works for any PM role — Main PM gets its own team's
    blocked/awaiting tasks; ``triage_all`` is the cross-team sweep.
    """
    env = await choreographer.triage(x_agent_id)
    return envelope_to_response(env, request)


@router.post("/submit_root")
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def submit_root(
    request: Request,
    body: SubmitRootRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.submit_root(x_agent_id, body.task_id, body.notes)
    return envelope_to_response(env, request)


@router.post("/complete")
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def complete(
    request: Request,
    body: CompleteRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.main_pm_complete(x_agent_id, body.task_id, body.notes)
    return envelope_to_response(env, request)


@router.post("/request_changes")
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def request_changes(
    request: Request,
    body: RequestChangesRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.request_changes(x_agent_id, body.task_id, body.issues)
    return envelope_to_response(env, request)


@router.post("/escalate_up")
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def escalate_up(
    request: Request,
    body: EscalateUpRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.escalate_up(x_agent_id, body.task_id, body.reason)
    return envelope_to_response(env, request)


@router.post("/escalate_to_ceo")
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def escalate_to_ceo(
    request: Request,
    body: EscalateToCeoRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.escalate_to_ceo(x_agent_id, body.task_id, body.reason)
    return envelope_to_response(env, request)


@router.post("/unblock")
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def unblock(
    request: Request,
    body: UnblockRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.unblock(
        x_agent_id, body.task_id, body.reason, restore=body.restore
    )
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
