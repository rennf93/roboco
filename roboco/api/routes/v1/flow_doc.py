"""Documenter intent-verb HTTP endpoints. Thin handlers; delegate to Choreographer."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request

from roboco.api.deps import get_choreographer
from roboco.api.routes.v1._role_dep import envelope_to_response, require_doc
from roboco.api.schemas.v1.flow import (
    ClaimDocTaskRequest,
    GiveMeWorkRequest,
    IAmBlockedRequest,
    IAmIdleRequest,
    IDocumentedRequest,
    ResumeRequest,
    UnclaimRequest,
)
from roboco.services.gateway.choreographer import Choreographer

router = APIRouter(
    prefix="/api/v1/flow/documenter",
    tags=["v1-flow-documenter"],
    dependencies=[require_doc],
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


@router.post("/claim_doc_task")
async def claim_doc_task(
    request: Request,
    body: ClaimDocTaskRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.claim_doc_task(x_agent_id, body.task_id)
    return envelope_to_response(env, request)


@router.post("/i_documented")
async def i_documented(
    request: Request,
    body: IDocumentedRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.i_documented(
        x_agent_id, body.task_id, body.notes, body.files
    )
    return envelope_to_response(env, request)


@router.post("/unclaim")
async def unclaim(
    request: Request,
    body: UnclaimRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.unclaim(x_agent_id, body.task_id)
    return envelope_to_response(env, request)


@router.post("/resume")
async def resume(
    request: Request,
    body: ResumeRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    env = await choreographer.resume(x_agent_id, body.task_id)
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


@router.post("/i_am_blocked")
async def i_am_blocked(
    request: Request,
    body: IAmBlockedRequest,
    x_agent_id: _AgentIdHeader,
    choreographer: _ChoreographerDep,
) -> dict:
    """F015: the documenter manifest registers ``i_am_blocked`` — surface the
    route so a blocked documenter's escape hatch returns an envelope instead of
    a 404."""
    env = await choreographer.i_am_blocked(
        x_agent_id,
        body.task_id,
        body.reason,
        blocker_type=body.blocker_type,
        what_needed=body.what_needed,
    )
    return envelope_to_response(env, request)
