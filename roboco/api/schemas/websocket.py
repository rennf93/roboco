"""
WebSocket API Schemas

Request/response models for WebSocket messages.
"""

from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel


class WSMessage(BaseModel):
    """Base WebSocket message."""

    type: str
    timestamp: datetime = datetime.now(UTC)


class WSAgentStream(WSMessage):
    """Agent stream chunk."""

    type: str = "agent.stream"
    agent_id: UUID
    chunk: str


class WSNotification(WSMessage):
    """Notification event."""

    type: str = "notification"
    notification_id: UUID
    notification_type: str
    subject: str
    priority: str
