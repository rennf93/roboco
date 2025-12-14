"""
WebSocket API Schemas

Request/response models for WebSocket messages.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel


@dataclass
class NewMessageBroadcast:
    """Data for broadcasting a new message."""

    channel_id: UUID
    session_id: UUID
    message_id: UUID
    agent_id: UUID
    content: str
    message_type: str


class WSMessage(BaseModel):
    """Base WebSocket message."""

    type: str
    timestamp: datetime = datetime.now(UTC)


class WSMessageNew(WSMessage):
    """New message event."""

    type: str = "message.new"
    message_id: UUID
    agent_id: UUID
    content: str
    message_type: str


class WSMessageEdit(WSMessage):
    """Message edited event."""

    type: str = "message.edit"
    message_id: UUID
    content: str


class WSMessageDelete(WSMessage):
    """Message deleted event."""

    type: str = "message.delete"
    message_id: UUID


class WSAgentStream(WSMessage):
    """Agent stream chunk."""

    type: str = "agent.stream"
    agent_id: UUID
    chunk: str


class WSSessionClosed(WSMessage):
    """Session closed event."""

    type: str = "session.closed"
    session_id: UUID
    reason: str


class WSNotification(WSMessage):
    """Notification event."""

    type: str = "notification"
    notification_id: UUID
    notification_type: str
    subject: str
    priority: str
