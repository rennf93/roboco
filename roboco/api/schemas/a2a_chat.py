"""
A2A Chat API Schemas

Request/response models for persistent A2A conversation endpoints.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from roboco.models.a2a import A2AConversationStatus, A2AMessageKind

# =============================================================================
# CONVERSATION SCHEMAS
# =============================================================================


class ConversationCreateRequest(BaseModel):
    """Request to create/start a conversation."""

    target_agent: str = Field(..., description="Agent slug to chat with")
    topic: str | None = Field(default=None, description="Optional topic")
    task_id: UUID | None = Field(default=None, description="Optional task link")
    initial_message: str = Field(..., min_length=1, max_length=10000)
    requires_response: bool = Field(default=False)


class ConversationCloseRequest(BaseModel):
    """Request to close a conversation."""

    resolution: str | None = Field(default=None, description="Why closing")


class ConversationResponse(BaseModel):
    """Conversation response."""

    id: UUID
    agent_a: str
    agent_b: str
    topic: str | None
    task_id: UUID | None
    status: A2AConversationStatus
    resolution: str | None
    message_count: int
    unread_by_a: int
    unread_by_b: int
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None


class ConversationSummaryResponse(BaseModel):
    """Summary for list views."""

    id: UUID
    other_agent: str
    topic: str | None
    task_id: UUID | None
    status: A2AConversationStatus
    message_count: int
    unread_count: int
    last_message_at: datetime | None
    last_message_preview: str | None


class ConversationListResponse(BaseModel):
    """List of conversation summaries."""

    items: list[ConversationSummaryResponse]
    total: int


class ListConversationsParams(BaseModel):
    """Query params for listing conversations."""

    status: A2AConversationStatus | None = None
    with_agent: str | None = None
    task_id: UUID | None = None
    limit: int = Field(50, ge=1, le=100)


# =============================================================================
# MESSAGE SCHEMAS
# =============================================================================


class MessageCreateRequest(BaseModel):
    """Request to send a message."""

    content: str = Field(..., min_length=1, max_length=10000)
    message_kind: A2AMessageKind = A2AMessageKind.MESSAGE
    response_to_id: UUID | None = None
    requires_response: bool = False


class MessageResponse(BaseModel):
    """A2A chat message response."""

    id: UUID
    conversation_id: UUID
    from_agent: str
    content: str
    message_kind: A2AMessageKind
    response_to_id: UUID | None
    requires_response: bool
    read_at: datetime | None
    created_at: datetime
    edited_at: datetime | None


class MessageListResponse(BaseModel):
    """List of messages."""

    items: list[MessageResponse]
    total: int
    has_more: bool


class ListMessagesParams(BaseModel):
    """Query params for listing messages."""

    limit: int = Field(100, ge=1, le=500)
    before: datetime | None = None


# =============================================================================
# INBOX SCHEMAS
# =============================================================================


class InboxSummaryResponse(BaseModel):
    """Inbox summary."""

    total_unread: int
    conversations_with_unread: int
    pending_responses: int
    unanswered_requests: int


# =============================================================================
# PAIRS SCHEMAS
# =============================================================================


class PairResponse(BaseModel):
    """Agent pair for frontend."""

    agent_a: str
    agent_b: str
    conversation_count: int
    total_unread: int
    last_activity: datetime | None


class PairListResponse(BaseModel):
    """List of pairs."""

    items: list[PairResponse]
    total: int


# =============================================================================
# ADMIN / LIVE VIEW SCHEMAS (CEO-only)
# =============================================================================


class AdminConversationSummaryResponse(BaseModel):
    """Conversation summary for the CEO's cross-agent live view."""

    id: UUID
    agent_a: str
    agent_b: str
    topic: str | None
    task_id: UUID | None
    status: A2AConversationStatus
    message_count: int
    last_message_at: datetime | None
    last_message_preview: str | None
    created_at: datetime
    updated_at: datetime


class AdminConversationListResponse(BaseModel):
    """List of admin conversation summaries."""

    items: list[AdminConversationSummaryResponse]
    total: int


class AdminReplyRequest(BaseModel):
    """Request for the CEO to chime into an existing A2A conversation."""

    to_agent: str = Field(..., description="Which participant to address")
    content: str = Field(..., min_length=1, max_length=10000)
    skill: str | None = None


# =============================================================================
# SWITCHBOARD SCHEMAS (CEO-only) — org-chart pair cards
# =============================================================================


class AdminPairResponse(BaseModel):
    """One agent pair for the CEO's A2A switchboard (org-chart pair cards)."""

    agent_a: str
    role_a: str
    team_a: str
    agent_b: str
    role_b: str
    team_b: str
    group_key: str
    conversation_id: UUID | None
    last_message_at: datetime | None
    message_count: int


class AdminPairListResponse(BaseModel):
    """List of switchboard pairs."""

    items: list[AdminPairResponse]
    total: int
