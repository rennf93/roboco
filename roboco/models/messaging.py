"""
Messaging Models

Data classes for messaging service requests.
"""

from dataclasses import dataclass
from uuid import UUID

from roboco.models.base import AgentRole, ChannelType, MessageType
from roboco.models.session import SessionScope


@dataclass
class ChannelCreateRequest:
    """Request to create a channel."""

    name: str
    slug: str
    channel_type: ChannelType
    description: str | None = None
    members: list[UUID] | None = None
    writers: list[UUID] | None = None
    silent_observers: list[UUID] | None = None
    is_private: bool = False


@dataclass
class GroupCreateRequest:
    """Request to create a group."""

    name: str
    channel_id: UUID
    allowed_roles: list[AgentRole] | None = None
    hierarchy_level: int = 4
    members: list[UUID] | None = None


@dataclass
class SessionCreateRequest:
    """Request to create a session."""

    group_id: UUID
    max_message_count: int | None = 100
    max_content_length: int | None = 50000
    timeout_seconds: int = 300
    scope: SessionScope = SessionScope.TASK


@dataclass
class MessageCreateRequest:
    """Request to send a message."""

    agent_id: UUID
    session_id: UUID
    content: str
    message_type: MessageType = MessageType.DIALOGUE
    reply_to: UUID | None = None
    mentions: list[UUID] | None = None
    task_id: UUID | None = None
    commit_ref: str | None = None
