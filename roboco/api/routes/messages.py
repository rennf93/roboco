"""
Message Routes

CRUD operations for messages within sessions.
"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from roboco.api.deps import CurrentAgentId, DbSession
from roboco.api.schemas.messages import (
    ListMessagesParams,
    MessageCreateRequest,
    MessageEditRequest,
    MessageListResponse,
    MessageResponse,
)
from roboco.db.tables import MessageTable, SessionTable
from roboco.services.messaging import (
    MessageCreateRequest as ServiceMessageRequest,
)
from roboco.services.messaging import (
    get_messaging_service,
)
from roboco.utils.converters import require_uuid, to_python_uuid, to_python_uuid_list

router = APIRouter()


# =============================================================================
# Routes
# =============================================================================


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
    """List messages in a session."""
    # Verify session exists
    session_result = await db.execute(
        select(SessionTable)
        .where(SessionTable.id == params.session_id)
        .options(selectinload(SessionTable.group))
    )
    session = session_result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    # Build query
    query = select(MessageTable).where(MessageTable.session_id == params.session_id)

    if params.before:
        query = query.where(MessageTable.timestamp < params.before)
    if params.after:
        query = query.where(MessageTable.timestamp > params.after)
    if params.type_filter:
        query = query.where(MessageTable.type == params.type_filter)

    # Order by timestamp descending (newest first) and limit
    query = query.order_by(MessageTable.timestamp.desc()).limit(params.limit + 1)

    result = await db.execute(query)
    messages = result.scalars().all()

    # Check if there are more messages
    has_more = len(messages) > params.limit
    if has_more:
        messages = messages[: params.limit]

    items = [
        MessageResponse(
            id=require_uuid(m.id),
            agent_id=require_uuid(m.agent_id),
            channel_id=require_uuid(m.channel_id),
            group_id=require_uuid(m.group_id),
            session_id=require_uuid(m.session_id),
            type=m.type,
            content=m.content,
            content_length=m.content_length,
            is_reply=m.is_reply,
            reply_to=to_python_uuid(m.reply_to),
            mentions=to_python_uuid_list(m.mentions),
            task_id=to_python_uuid(m.task_id),
            commit_ref=m.commit_ref,
            timestamp=m.timestamp,
            edited_at=m.edited_at,
            was_edited=len(m.edit_history) > 0,
        )
        for m in messages
    ]

    return MessageListResponse(
        items=items,
        total=len(items),
        has_more=has_more,
    )


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
    result = await db.execute(select(MessageTable).where(MessageTable.id == message_id))
    message = result.scalar_one_or_none()

    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found",
        )

    return MessageResponse(
        id=require_uuid(message.id),
        agent_id=require_uuid(message.agent_id),
        channel_id=require_uuid(message.channel_id),
        group_id=require_uuid(message.group_id),
        session_id=require_uuid(message.session_id),
        type=message.type,
        content=message.content,
        content_length=message.content_length,
        is_reply=message.is_reply,
        reply_to=to_python_uuid(message.reply_to),
        mentions=to_python_uuid_list(message.mentions),
        task_id=to_python_uuid(message.task_id),
        commit_ref=message.commit_ref,
        timestamp=message.timestamp,
        edited_at=message.edited_at,
        was_edited=len(message.edit_history) > 0,
    )


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
    """Send a message to a session using MessagingService."""
    service = get_messaging_service(db)

    # Convert route request to service request
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
        message = await service.send_message(service_request)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    return MessageResponse(
        id=require_uuid(message.id),
        agent_id=require_uuid(message.agent_id),
        channel_id=require_uuid(message.channel_id),
        group_id=require_uuid(message.group_id),
        session_id=require_uuid(message.session_id),
        type=message.type,
        content=message.content,
        content_length=message.content_length,
        is_reply=message.is_reply,
        reply_to=to_python_uuid(message.reply_to),
        mentions=to_python_uuid_list(message.mentions),
        task_id=to_python_uuid(message.task_id),
        commit_ref=message.commit_ref,
        timestamp=message.timestamp,
        edited_at=message.edited_at,
        was_edited=len(message.edit_history) > 0 if message.edit_history else False,
    )


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
    """Edit a message."""
    result = await db.execute(select(MessageTable).where(MessageTable.id == message_id))
    message = result.scalar_one_or_none()

    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found",
        )

    # Only author can edit
    if message.agent_id != agent_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only edit your own messages",
        )

    # Save to edit history
    edit_record = {
        "edited_at": datetime.now(UTC).isoformat(),
        "previous_content": message.content,
        "edit_reason": data.reason,
    }
    message.edit_history = [*message.edit_history, edit_record]

    # Update content
    old_length = message.content_length
    message.content = data.content
    message.content_length = len(data.content)
    message.edited_at = datetime.now(UTC)

    # Update session content length
    session_result = await db.execute(
        select(SessionTable).where(SessionTable.id == message.session_id)
    )
    session = session_result.scalar_one_or_none()
    if session:
        session.total_content_length += message.content_length - old_length

    await db.flush()

    return MessageResponse(
        id=require_uuid(message.id),
        agent_id=require_uuid(message.agent_id),
        channel_id=require_uuid(message.channel_id),
        group_id=require_uuid(message.group_id),
        session_id=require_uuid(message.session_id),
        type=message.type,
        content=message.content,
        content_length=message.content_length,
        is_reply=message.is_reply,
        reply_to=to_python_uuid(message.reply_to),
        mentions=to_python_uuid_list(message.mentions),
        task_id=to_python_uuid(message.task_id),
        commit_ref=message.commit_ref,
        timestamp=message.timestamp,
        edited_at=message.edited_at,
        was_edited=True,
    )


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
    """Delete a message."""
    result = await db.execute(select(MessageTable).where(MessageTable.id == message_id))
    message = result.scalar_one_or_none()

    if not message:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Message not found",
        )

    # Only author can delete
    if message.agent_id != agent_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only delete your own messages",
        )

    # Update session stats
    session_result = await db.execute(
        select(SessionTable).where(SessionTable.id == message.session_id)
    )
    session = session_result.scalar_one_or_none()
    if session:
        session.message_count -= 1
        session.total_content_length -= message.content_length

    # Delete
    await db.delete(message)
    await db.flush()
