"""
Message Routes

CRUD for messages within sessions. Thin HTTP plumbing — all DB state lives
in `MessagingService`.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from roboco.api.deps import CurrentAgentId, DbSession
from roboco.api.schemas.messages import (
    ListMessagesParams,
    MessageCreateRequest,
    MessageEditRequest,
    MessageListResponse,
    MessageResponse,
    message_list_to_response,
    message_to_response,
)
from roboco.services.base import NotFoundError
from roboco.services.messaging import (
    MessageCreateRequest as ServiceMessageRequest,
)
from roboco.services.messaging import (
    get_messaging_service,
)

router = APIRouter()

_MAX_MSG_CHARS = 10_000


def _assert_send_content(raw: str | None) -> None:
    """Validate request content before delegating to the service."""
    trimmed = (raw or "").strip()
    if not trimmed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="EMPTY_MESSAGE: message content cannot be blank.",
        )
    if len(raw or "") > _MAX_MSG_CHARS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"MESSAGE_TOO_LONG: {len(raw or '')} chars exceeds "
                f"{_MAX_MSG_CHARS}. Split the message or link a doc."
            ),
        )


@router.get(
    "",
    response_model=MessageListResponse,
    summary="List messages",
    description="List messages in a session.",
)
async def list_messages(
    db: DbSession,
    _agent_id: CurrentAgentId,
    params: Annotated[ListMessagesParams, Depends()],
) -> MessageListResponse:
    """List messages in a session (session existence is checked in the service)."""
    messaging = get_messaging_service(db)
    try:
        messages, has_more = await messaging.list_messages_for_session(
            session_id=params.session_id,
            before=params.before,
            after=params.after,
            message_type=params.type_filter,
            limit=params.limit,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

    items = message_list_to_response(list(messages))
    return MessageListResponse(items=items, total=len(items), has_more=has_more)


@router.get(
    "/{message_id}",
    response_model=MessageResponse,
    summary="Get message",
    description="Get a specific message.",
)
async def get_message(
    db: DbSession,
    _agent_id: CurrentAgentId,
    message_id: UUID,
) -> MessageResponse:
    """Get a message by ID."""
    messaging = get_messaging_service(db)
    try:
        message = await messaging.get_message_or_raise(message_id)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return message_to_response(message)


@router.post(
    "",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Send message",
    description="Send a message to a session.",
)
async def send_message(
    db: DbSession,
    agent_id: CurrentAgentId,
    data: MessageCreateRequest,
) -> MessageResponse:
    """Send a message via MessagingService."""
    _assert_send_content(data.content)
    messaging = get_messaging_service(db)

    service_request = ServiceMessageRequest(
        agent_id=agent_id,
        session_id=data.session_id,
        content=data.content,
        message_type=data.type,
        reply_to=data.reply_to,
        mentions=data.mentions if data.mentions else [],
        task_id=data.task_id,
        commit_ref=data.commit_ref,
    )
    try:
        message = await messaging.send_message(service_request)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
    return message_to_response(message)


@router.patch(
    "/{message_id}",
    response_model=MessageResponse,
    summary="Edit message",
    description="Edit a message. Only the author can edit their messages.",
)
async def edit_message(
    db: DbSession,
    agent_id: CurrentAgentId,
    message_id: UUID,
    data: MessageEditRequest,
) -> MessageResponse:
    """Edit a message (author-only)."""
    messaging = get_messaging_service(db)
    try:
        message = await messaging.edit_message_or_raise(
            message_id=message_id,
            agent_id=agent_id,
            new_content=data.content,
            edit_reason=data.reason,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    return message_to_response(message, was_edited=True)


@router.delete(
    "/{message_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete message",
    description="Delete a message. Only the author can delete their messages.",
)
async def delete_message(
    db: DbSession,
    agent_id: CurrentAgentId,
    message_id: UUID,
) -> None:
    """Delete a message (author-only)."""
    messaging = get_messaging_service(db)
    try:
        await messaging.delete_message_or_raise(
            message_id=message_id, agent_id=agent_id
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
