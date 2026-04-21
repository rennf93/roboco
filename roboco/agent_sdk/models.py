"""
Agent SDK Models.

Pydantic models for A2A messaging between agents.
"""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class MessagePriority(StrEnum):
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


class BudgetToolCalledRequest(BaseModel):
    """Hook-submitted record that a tool was invoked."""

    tool: str = Field(..., description="Tool name (MCP prefix stripped or not)")
    args_hash: str = Field(
        ...,
        description="Stable hash of tool input; used for loop detection",
    )


class BudgetStatus(BaseModel):
    """Current per-session budget state."""

    total: int = Field(default=0, description="Total tool calls this session")
    by_tool: dict[str, int] = Field(
        default_factory=dict, description="Per-tool call counts"
    )
    warn: bool = Field(default=False, description="Soft threshold reached")
    halt: bool = Field(default=False, description="Hard cap breached")
    loop: bool = Field(
        default=False,
        description="Identical tool+args hash repeated above loop_threshold in window",
    )
    warn_threshold: int = Field(default=0)
    halt_threshold: int = Field(default=0)
    loop_threshold: int = Field(default=0)
    loop_window: int = Field(default=0)


class TerminalToolRecordRequest(BaseModel):
    """Hook-submitted record that any tool finished (to track recency)."""

    tool: str = Field(..., description="Tool name")


class TerminalStatus(BaseModel):
    """Terminal-tool + stop-attempt state."""

    last_tool: str | None = Field(default=None)
    recent_tools: list[str] = Field(
        default_factory=list,
        description="Last N tools called (newest last)",
    )
    had_terminal_recently: bool = Field(
        default=False,
        description="Whether a terminal tool was called in the last 5 tool events",
    )
    stop_attempts: int = Field(
        default=0, description="Times Stop has been attempted without a terminal tool"
    )
    stop_allowance: int = Field(
        default=1, description="Attempts allowed before SDK auto-substitutes"
    )


class PostMortemRequest(BaseModel):
    """SessionEnd post-mortem payload submitted by the hook."""

    terminal_tool: str | None = Field(default=None)
    duration_seconds: float = Field(default=0.0)
    tools_called: int = Field(default=0)
    loop_triggered: bool = Field(default=False)
    halt_triggered: bool = Field(default=False)
    reason: str = Field(default="stopped")
