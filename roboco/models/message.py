"""
Message Models

Messages are extracted from agent streams and stored for communication,
context, and RAG purposes.
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from pydantic import Field

from roboco.models.base import (
    MessageType,
    RobocoBase,
    TimestampMixin,
)

# =============================================================================
# SUPPORTING MODELS
# =============================================================================


class RawStream(RobocoBase):
    """
    WebSocket payload - ephemeral.

    Raw chunks from agent LLM output before extraction.
    """

    connection_id: UUID = Field(..., description="WebSocket connection ID")
    agent_id: UUID = Field(..., description="Agent producing the stream")
    channel_id: UUID = Field(..., description="Target channel")
    chunk: str = Field(..., description="Raw LLM output chunk")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


# =============================================================================
# MAIN MESSAGE MODEL
# =============================================================================


class ExtractedMessage(TimestampMixin):
    """
    Processed, stored message extracted from agent streams.

    Messages are the atomic unit of communication, indexed
    for search and RAG retrieval.
    """

    # Identity
    id: UUID = Field(default_factory=uuid4, description="Message ID (msg_id)")

    # Source & Context
    agent_id: UUID = Field(..., description="Agent who sent the message")
    channel_id: UUID = Field(..., description="Channel the message is in")
    group_id: UUID = Field(..., description="Group the message belongs to")
    session_id: UUID = Field(..., description="Session ID (sesh_id)")

    # Content
    type: MessageType = Field(..., description="Type of extracted message")
    content: str = Field(..., description="Message content")
    content_length: int = Field(..., ge=0, description="Character count")

    # Threading
    is_reply: bool = Field(default=False, description="Whether this is a reply")
    reply_to: UUID | None = Field(
        default=None, description="Parent message ID if is_reply"
    )

    # Mentions (for in-channel references, NOT notifications)
    mentions: list[UUID] = Field(
        default_factory=list, description="Agent IDs mentioned in message"
    )

    # Task Context
    task_id: UUID | None = Field(
        default=None, description="Related task ID if applicable"
    )
    commit_ref: str | None = Field(
        default=None, description="Related commit hash if applicable"
    )

    # Metadata
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))

    # Embedding for RAG (stored as list of floats, actual Vector type in DB)
    embedding: list[float] | None = Field(
        default=None, description="Vector embedding for RAG"
    )

    # Extraction metadata
    confidence: float = Field(
        default=1.0, ge=0.0, le=1.0, description="Extraction confidence"
    )
    raw_excerpt: str | None = Field(
        default=None, description="Original text excerpt before extraction"
    )

    # Edit tracking
    edited_at: datetime | None = None
    edit_history: list[dict[str, Any]] = Field(
        default_factory=list, description="Previous versions if edited"
    )

    # NOTE: Message editing is dead — no writer ever appends to edit_history,
    # and ExtractedMessage is never persisted to a DB table (extraction is a
    # pure in-memory pipeline). To check if a message was edited:
    # len(message.edit_history) > 0
