"""DevOps intent-verb HTTP endpoints. Thin handlers; delegate to Choreographer.

The floating DevOps agent's route (``/api/v1/flow/devops/<verb>`` - the flow
server's ``_ROUTE_PREFIX`` passes the role name through). Two lanes ride this
surface: the authoring lane (the developer lifecycle subset: claim through
``i_am_done`` + ``sync_branch``) and the infra review gate (``claim_gate_review``
+ the verdict verbs). Nothing here spawns or authorizes anything on its own -
the ``ROBOCO_DEVOPS_ENABLED`` flag gates spawning, and every verb's choreographer
body re-checks role/state.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from guard_core.handlers.behavior_handler import BehaviorRule

from roboco.api.deps import get_choreographer
from roboco.api.routes.v1._role_dep import envelope_to_response, require_devops
from roboco.api.schemas.v1.flow import (
    ClaimGateReviewRequest,
    GiveMeWorkRequest,
    IAmDoneRequest,
    IAmIdleRequest,
    IWillWorkOnRequest,
    OpenPrRequest,
    PrFailRequest,
    PrPassRequest,
    RecordDevopsReviewRequest,
    SyncBranchRequest,
    UnclaimRequest,
)
from roboco.security import guard_deco, mesh_scanned
from roboco.services.gateway.choreographer import Choreographer

_RUNAWAY_RULES = [
    BehaviorRule(rule_type="frequency", threshold=120, window=60, action="log")
]

router = APIRouter(
    prefix="/api/v1/flow/devops",
    tags=["v1-flow-devops"],
    dependencies=[require_devops],
)


_AgentIdHeader = Annotated[UUID, Header(alias="X-Agent-ID")]
_ChoreographerDep = Annotated[Choreographer, Depends(get_choreographer)]


@router.post("/give_me_work")
@mesh_scanned
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


@router.post("/i_will_work_on")
@mesh_scanned
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def i_will_work_on(
    request: Request,
    body: IWillWorkOnRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.i_will_work_on(
        agent_id=x_agent_id,
        task_id=body.task_id,
        plan=body.plan,
        steps=body.steps,
        technical_considerations=body.technical_considerations,
        risks=body.risks,
        open_questions=body.open_questions,
    )
    return envelope_to_response(env, request)


@router.post("/open_pr")
@mesh_scanned
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def open_pr(
    request: Request,
    body: OpenPrRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.open_pr(x_agent_id, body.task_id)
    return envelope_to_response(env, request)


@router.post("/i_am_done")
@mesh_scanned
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def i_am_done(
    request: Request,
    body: IAmDoneRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.i_am_done(
        x_agent_id,
        body.task_id,
        body.notes,
        resolved_findings=[r.model_dump() for r in body.resolved_findings],
    )
    return envelope_to_response(env, request)


@router.post("/unclaim")
@mesh_scanned
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


@router.post("/sync_branch")
@mesh_scanned
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def sync_branch(
    request: Request,
    body: SyncBranchRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.sync_branch(x_agent_id, body.task_id, stash=body.stash)
    return envelope_to_response(env, request)


@router.post("/i_am_idle")
@mesh_scanned
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


@router.post("/claim_gate_review")
@mesh_scanned
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def claim_gate_review(
    request: Request,
    body: ClaimGateReviewRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.claim_gate_review(x_agent_id, body.task_id)
    return envelope_to_response(env, request)


@router.post("/record_devops_review")
@mesh_scanned
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def record_devops_review(
    request: Request,
    body: RecordDevopsReviewRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.record_devops_review(x_agent_id, body.task_id, body.notes)
    return envelope_to_response(env, request)


@router.post("/pr_pass")
@mesh_scanned
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def pr_pass(
    request: Request,
    body: PrPassRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.pr_pass(x_agent_id, body.task_id, body.notes)
    return envelope_to_response(env, request)


@router.post("/pr_fail")
@mesh_scanned
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.behavior_analysis(_RUNAWAY_RULES)
async def pr_fail(
    request: Request,
    body: PrFailRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.pr_fail(
        x_agent_id, body.task_id, body.issues, body.findings
    )
    return envelope_to_response(env, request)
