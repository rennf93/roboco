"""
Messages API Schemas

Request/response models for message endpoints.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from roboco.models import MessageType


class ListMessagesParams(BaseModel):
    """Query parameters for listing messages."""

    session_id: UUID
    before: datetime | None = None
    after: datetime | None = None
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
