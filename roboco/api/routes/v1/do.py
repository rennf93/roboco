"""Content-tool HTTP endpoints. Thin handlers; delegate to ContentActions."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request

from roboco.api.deps import get_content_actions
from roboco.api.routes.v1._role_dep import envelope_to_response
from roboco.api.schemas.v1.do import (
    ChannelsRequest,
    CommitRequest,
    DmRequest,
    EvidenceRequest,
    LinkSessionRequest,
    NoteRequest,
    NotifyAckRequest,
    NotifyGetRequest,
    NotifyListRequest,
    NotifyRequest,
    OpenSessionRequest,
    ProgressRequest,
    PRUpdateRequest,
    ReadMessagesRequest,
    SayRequest,
)
from roboco.services.gateway.content_actions import ContentActions

router = APIRouter(prefix="/api/v1/do", tags=["v1-do"])

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


# ---------------------------------------------------------------------------
# Wave 1 — pre-gateway parity
# ---------------------------------------------------------------------------


@router.post("/progress")
async def do_progress(
    request: Request,
    body: ProgressRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.progress(
        agent_id=x_agent_id,
        task_id=body.task_id,
        message=body.message,
        plan_step=body.plan_step,
        percentage=body.percentage,
    )
    return envelope_to_response(env, request)


@router.post("/open_session")
async def do_open_session(
    request: Request,
    body: OpenSessionRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.open_session(
        agent_id=x_agent_id,
        task_id=body.task_id,
        channel=body.channel,
        topic=body.topic,
        relationship_type=body.relationship_type,
        group_id=body.group_id,
    )
    return envelope_to_response(env, request)


@router.post("/link_session")
async def do_link_session(
    request: Request,
    body: LinkSessionRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.link_session(
        agent_id=x_agent_id,
        session_id=body.session_id,
        task_id=body.task_id,
        is_primary=body.is_primary,
        relationship_type=body.relationship_type,
    )
    return envelope_to_response(env, request)


@router.post("/notify_list")
async def do_notify_list(
    request: Request,
    body: NotifyListRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.notify_list(
        agent_id=x_agent_id,
        unread_only=body.unread_only,
        pending_ack_only=body.pending_ack_only,
        limit=body.limit,
    )
    return envelope_to_response(env, request)


@router.post("/notify_get")
async def do_notify_get(
    request: Request,
    body: NotifyGetRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.notify_get(
        agent_id=x_agent_id,
        notification_id=body.notification_id,
    )
    return envelope_to_response(env, request)


@router.post("/notify_ack")
async def do_notify_ack(
    request: Request,
    body: NotifyAckRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.notify_ack(
        agent_id=x_agent_id,
        notification_id=body.notification_id,
    )
    return envelope_to_response(env, request)


@router.post("/read_messages")
async def do_read_messages(
    request: Request,
    _body: ReadMessagesRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.read_messages(agent_id=x_agent_id)
    return envelope_to_response(env, request)


@router.post("/channels")
async def do_channels(
    request: Request,
    _body: ChannelsRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.channels(agent_id=x_agent_id)
    return envelope_to_response(env, request)


@router.post("/pr_update")
async def do_pr_update(
    request: Request,
    body: PRUpdateRequest,
    x_agent_id: _AgentIdHeader,
    actions: _ContentActionsDep,
) -> dict:
    env = await actions.pr_update(
        agent_id=x_agent_id,
        task_id=body.task_id,
        title=body.title,
        body=body.body,
        reviewers=body.reviewers,
    )
    return envelope_to_response(env, request)
