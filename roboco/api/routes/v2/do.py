"""Content-tool HTTP endpoints. Thin handlers; delegate to ContentActions."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request

from roboco.api.deps import get_content_actions
from roboco.api.routes.v2._role_dep import envelope_to_response
from roboco.api.schemas.v2.do import (
    CommitRequest,
    DmRequest,
    EvidenceRequest,
    NoteRequest,
    NotifyRequest,
    SayRequest,
)
from roboco.services.gateway.content_actions import ContentActions

router = APIRouter(prefix="/api/v2/do", tags=["v2-do"])

_AgentIdHeader = Annotated[UUID, Header(alias="X-Agent-ID")]
_ContentActionsDep = Annotated[ContentActions, Depends(get_content_actions)]


@router.post("/commit")
async def do_commit(
    request: Request,
    body: CommitRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.commit(
        agent_id=x_agent_id,
        message=body.message,
        files=body.files,
    )
    return envelope_to_response(env, request)


@router.post("/note")
async def do_note(
    request: Request,
    body: NoteRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.note(
        agent_id=x_agent_id,
        text=body.text,
        scope=body.scope,
        task_id=body.task_id,
        structured={
            "title": body.title,
            "context": body.context,
            "options": body.options,
            "chosen": body.chosen,
            "rationale": body.rationale,
            "consequences": body.consequences,
            "what_done": body.what_done,
            "what_learned": body.what_learned,
            "what_struggled": body.what_struggled,
            "next_steps": body.next_steps,
        },
    )
    return envelope_to_response(env, request)


@router.post("/say")
async def do_say(
    request: Request,
    body: SayRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.say(
        agent_id=x_agent_id,
        channel=body.channel,
        text=body.text,
        task_id=body.task_id,
    )
    return envelope_to_response(env, request)


@router.post("/dm")
async def do_dm(
    request: Request,
    body: DmRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.dm(
        agent_id=x_agent_id,
        recipient=body.recipient,
        text=body.text,
        task_id=body.task_id,
        skill=body.skill,
    )
    return envelope_to_response(env, request)


@router.post("/notify")
async def do_notify(
    request: Request,
    body: NotifyRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.notify(
        agent_id=x_agent_id,
        target=body.target,
        text=body.text,
        priority=body.priority,
        task_id=body.task_id,
    )
    return envelope_to_response(env, request)


@router.post("/evidence")
async def do_evidence(
    request: Request,
    body: EvidenceRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.evidence(agent_id=x_agent_id, task_id=body.task_id)
    return envelope_to_response(env, request)
