"""
Agent SDK Models.

Pydantic models for A2A messaging between agents.
"""

from datetime import UTC, datetime
from enum import Enum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class MessagePriority(str, Enum):
    """Priority level for A2A messages."""

    URGENT = "urgent"
    NORMAL = "normal"


class A2AMessage(BaseModel):
    """
    A2A message received by SDK server.

    Represents a message from one agent to another about a specific task.
    """

    id: UUID = Field(default_factory=uuid4)
    from_agent: str = Field(..., description="Sender agent slug")
    to_agent: str = Field(..., description="Recipient agent slug")
    task_id: str = Field(..., description="Related task ID")
    skill: str = Field(..., description="Requested skill (e.g., code_review)")
    content: str = Field(..., description="Message content")
    priority: MessagePriority = Field(default=MessagePriority.NORMAL)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    acked: bool = Field(default=False, description="Whether message was acknowledged")


class SendRequest(BaseModel):
    """Request to send an A2A message."""

    target_agent: str = Field(..., description="Target agent slug")
    skill: str = Field(..., description="Skill being requested")
    message: str = Field(..., description="Message content")
    task_id: str = Field(..., description="Related task ID")
    urgent: bool = Field(default=False, description="Whether this is urgent")


class SendResponse(BaseModel):
    """Response from sending an A2A message."""

    status: str = Field(..., description="Status: sent, queued, failed")
    message_id: str = Field(..., description="Message UUID")
    delivery: str = Field(..., description="Delivery method: direct, notification")


class InboxResponse(BaseModel):
    """Response from polling the inbox."""

    messages: list[A2AMessage] = Field(default_factory=list)
    count: int = Field(default=0, description="Number of messages returned")


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = Field(default="ok")
    agent_id: str = Field(..., description="This agent's ID")
