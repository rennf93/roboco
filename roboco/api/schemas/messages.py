"""
Messages API Schemas

Request/response models for message endpoints.
"""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, Field

from roboco.models import MessageType
from roboco.utils.converters import require_uuid, to_python_uuid, to_python_uuid_list

if TYPE_CHECKING:
    from roboco.db.tables import MessageTable


class ListMessagesParams(BaseModel):
    """Query parameters for listing messages."""

    session_id: UUID
    before: datetime | None = None
    after: datetime | None = None
    # Keyset-pagination tie-breakers: pass the last (before) / first (after)
    # message's id alongside its timestamp so equal-timestamp messages are not
    # skipped across pages. Optional — a timestamp-only cursor keeps the
    # legacy strict-inequality behavior.
    before_id: UUID | None = None
    after_id: UUID | None = None
    type_filter: MessageType | None = None
    limit: int = Field(50, ge=1, le=100)


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
# RESPONSE TRANSFORMERS
# =============================================================================


def message_to_response(
    message: "MessageTable",
    *,
    was_edited: bool | None = None,
) -> MessageResponse:
    """
    Transform a MessageTable to MessageResponse.

    Args:
        message: The database message object
        was_edited: Override for was_edited flag (useful when already known)

    Returns:
        MessageResponse for the API
    """
    # Determine was_edited flag
    if was_edited is None:
        was_edited = bool(message.edit_history and len(message.edit_history) > 0)

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
        was_edited=was_edited,
    )


def message_list_to_response(messages: list["MessageTable"]) -> list[MessageResponse]:
    """Transform a list of MessageTable to MessageResponse list."""
    return [message_to_response(m) for m in messages]
