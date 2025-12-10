"""
Message Routes

CRUD operations for messages within sessions.
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from roboco.api.deps import CurrentAgentId, DbSession
from roboco.db.tables import ChannelTable, GroupTable, MessageTable, SessionTable
from roboco.models import MessageCreate, MessageType, SessionStatus

router = APIRouter()


# =============================================================================
# Response Models
# =============================================================================


class MessageResponse(BaseModel):
    """Message response."""

    id: UUID
    agent_id: UUID
    channel_id: UUID
    group_id: UUID
    session_id: UUID
    type: MessageType
    content: str
    content_length: int
    is_reply: bool
    reply_to: UUID | None
    mentions: list[UUID]
    task_id: UUID | None
    commit_ref: str | None
    timestamp: datetime
    edited_at: datetime | None
    was_edited: bool


class MessageListResponse(BaseModel):
    """List of messages."""

    items: list[MessageResponse]
    total: int
    has_more: bool


class MessageCreateRequest(BaseModel):
    """Request to create a message."""

    session_id: UUID
    type: MessageType
    content: str = Field(..., min_length=1, max_length=10000)
    is_reply: bool = False
    reply_to: UUID | None = None
    mentions: list[UUID] = Field(default_factory=list)
    task_id: UUID | None = None
    commit_ref: str | None = None


class MessageEditRequest(BaseModel):
    """Request to edit a message."""

    content: str = Field(..., min_length=1, max_length=10000)
    reason: str | None = None


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
    agent_id: CurrentAgentId,
    session_id: UUID = Query(...),
    before: datetime | None = None,
    after: datetime | None = None,
    type_filter: MessageType | None = None,
    limit: int = Query(50, ge=1, le=100),
) -> MessageListResponse:
    """List messages in a session."""
    # Verify session exists
    session_result = await db.execute(
        select(SessionTable)
        .where(SessionTable.id == session_id)
        .options(selectinload(SessionTable.group))
    )
    session = session_result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    # Build query
    query = select(MessageTable).where(MessageTable.session_id == session_id)

    if before:
        query = query.where(MessageTable.timestamp < before)
    if after:
        query = query.where(MessageTable.timestamp > after)
    if type_filter:
        query = query.where(MessageTable.type == type_filter)

    # Order by timestamp descending (newest first) and limit
    query = query.order_by(MessageTable.timestamp.desc()).limit(limit + 1)

    result = await db.execute(query)
    messages = result.scalars().all()

    # Check if there are more messages
    has_more = len(messages) > limit
    if has_more:
        messages = messages[:limit]

    items = [
        MessageResponse(
            id=m.id,
            agent_id=m.agent_id,
            channel_id=m.channel_id,
            group_id=m.group_id,
            session_id=m.session_id,
            type=m.type,
            content=m.content,
            content_length=m.content_length,
            is_reply=m.is_reply,
            reply_to=m.reply_to,
            mentions=m.mentions,
            task_id=m.task_id,
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
    agent_id: CurrentAgentId,
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
        id=message.id,
        agent_id=message.agent_id,
        channel_id=message.channel_id,
        group_id=message.group_id,
        session_id=message.session_id,
        type=message.type,
        content=message.content,
        content_length=message.content_length,
        is_reply=message.is_reply,
        reply_to=message.reply_to,
        mentions=message.mentions,
        task_id=message.task_id,
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
    """Send a message to a session."""
    # Get session with group and channel
    session_result = await db.execute(
        select(SessionTable)
        .where(SessionTable.id == data.session_id)
        .options(selectinload(SessionTable.group))
    )
    session = session_result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    if session.status != SessionStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Session is not active",
        )

    # Get channel for access check
    group = session.group
    channel_result = await db.execute(
        select(ChannelTable).where(ChannelTable.id == group.channel_id)
    )
    channel = channel_result.scalar_one_or_none()

    if not channel or agent_id not in channel.writers:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have write access to this channel",
        )

    # Verify reply_to if provided
    if data.reply_to:
        reply_result = await db.execute(
            select(MessageTable).where(
                MessageTable.id == data.reply_to,
                MessageTable.session_id == data.session_id,
            )
        )
        if not reply_result.scalar_one_or_none():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Reply target message not found in this session",
            )

    # Create message
    content_length = len(data.content)
    message = MessageTable(
        agent_id=agent_id,
        channel_id=channel.id,
        group_id=group.id,
        session_id=session.id,
        type=data.type,
        content=data.content,
        content_length=content_length,
        is_reply=data.is_reply,
        reply_to=data.reply_to,
        mentions=data.mentions,
        task_id=data.task_id,
        commit_ref=data.commit_ref,
    )

    db.add(message)

    # Update session stats
    session.message_count += 1
    session.total_content_length += content_length
    session.last_activity_at = datetime.utcnow()

    # Update group stats
    group.total_messages += 1
    group.last_activity = datetime.utcnow()

    # Update channel stats
    channel.message_count += 1
    channel.last_activity = datetime.utcnow()

    # Check if session should be closed
    should_close = False
    if session.max_message_count and session.message_count >= session.max_message_count:
        should_close = True
    if (
        session.max_content_length
        and session.total_content_length >= session.max_content_length
    ):
        should_close = True

    if should_close:
        session.status = SessionStatus.CLOSED
        session.closed_at = datetime.utcnow()
        group.active_session_id = None

    await db.flush()

    return MessageResponse(
        id=message.id,
        agent_id=message.agent_id,
        channel_id=message.channel_id,
        group_id=message.group_id,
        session_id=message.session_id,
        type=message.type,
        content=message.content,
        content_length=message.content_length,
        is_reply=message.is_reply,
        reply_to=message.reply_to,
        mentions=message.mentions,
        task_id=message.task_id,
        commit_ref=message.commit_ref,
        timestamp=message.timestamp,
        edited_at=message.edited_at,
        was_edited=False,
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
        "edited_at": datetime.utcnow().isoformat(),
        "previous_content": message.content,
        "edit_reason": data.reason,
    }
    message.edit_history = [*message.edit_history, edit_record]

    # Update content
    old_length = message.content_length
    message.content = data.content
    message.content_length = len(data.content)
    message.edited_at = datetime.utcnow()

    # Update session content length
    session_result = await db.execute(
        select(SessionTable).where(SessionTable.id == message.session_id)
    )
    session = session_result.scalar_one_or_none()
    if session:
        session.total_content_length += message.content_length - old_length

    await db.flush()

    return MessageResponse(
        id=message.id,
        agent_id=message.agent_id,
        channel_id=message.channel_id,
        group_id=message.group_id,
        session_id=message.session_id,
        type=message.type,
        content=message.content,
        content_length=message.content_length,
        is_reply=message.is_reply,
        reply_to=message.reply_to,
        mentions=message.mentions,
        task_id=message.task_id,
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
