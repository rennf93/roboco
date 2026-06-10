"""
Agent SDK Models.

Pydantic models for A2A messaging between agents.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal
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
    loop_action: Literal["warn", "halt"] = Field(
        default="halt",
        description=(
            "What the post-tool hook should do when loop=True. "
            "'halt' -> hook exits 1 to deny the wrapping tool call; "
            "'warn' -> hook prints [Loop] and exits 0 (legacy behaviour). "
            "Sourced from foundation.BudgetPolicy.loop_action; env-overridable "
            "via ROBOCO_AGENT_LOOP_ACTION."
        ),
    )


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


class VerbAttemptRequest(BaseModel):
    """Per-verb circuit-breaker attempt record.

    Posted by the agent's response-handling layer when a gateway verb
    call returns a rejection envelope (`tracing_gap`, `invalid_state`,
    `not_authorized`, `incomplete_input`). Successful (`ok`) calls must
    NOT be posted — only rejections count toward the circuit breaker.
    """

    verb: str = Field(..., description="Gateway verb name, e.g. i_am_done")
    task_id: str | None = Field(
        default=None,
        description=(
            "Task this verb call was scoped to. None for verbs that operate "
            "without a task — those track per-verb only."
        ),
    )
    rejection_kind: str = Field(
        ...,
        description=(
            "Envelope error kind: tracing_gap | invalid_state | "
            "not_authorized | incomplete_input"
        ),
    )


class VerbCircuitStatus(BaseModel):
    """Response from /verb/attempted — breaker state for this (verb, task_id) key."""

    verb: str = Field(..., description="Verb that was recorded")
    task_id: str | None = Field(default=None)
    attempts: int = Field(
        default=0,
        description="Rejections counted in the current 60s window for this key",
    )
    limit: int | None = Field(
        default=None,
        description=(
            "Per-verb cap from foundation.retry_limit_for(verb). None means "
            "the verb is in UNLIMITED_RETRY_VERBS — the breaker never trips."
        ),
    )
    window_seconds: int = Field(
        default=60, description="Sliding-window size used by the tracker"
    )
    open: bool = Field(
        default=False,
        description="True if attempts >= limit — agent must stop calling this verb",
    )
    circuit_envelope: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Populated only when open=True. Wire-format Envelope.circuit_open "
            "the SDK consumer should return to the agent in place of the "
            "next gateway call."
        ),
    )
