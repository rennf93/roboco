"""Documenter intent-verb HTTP endpoints. Thin handlers; delegate to Choreographer."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header

from roboco.api.deps import get_choreographer
from roboco.api.routes.v2._role_dep import require_doc
from roboco.api.schemas.v2.flow import (
    ClaimDocTaskRequest,
    GiveMeWorkRequest,
    IAmIdleRequest,
    IDocumentedRequest,
    ResumeRequest,
    UnclaimRequest,
)
from roboco.services.gateway.choreographer import Choreographer

router = APIRouter(
    prefix="/api/v2/flow/documenter",
    tags=["v2-flow-documenter"],
    dependencies=[require_doc],
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


@router.post("/claim_doc_task")
async def claim_doc_task(
    body: ClaimDocTaskRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.claim_doc_task(x_agent_id, body.task_id)
    return env.as_dict()


@router.post("/i_documented")
async def i_documented(
    body: IDocumentedRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.i_documented(
        x_agent_id, body.task_id, body.notes, body.files
    )
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
