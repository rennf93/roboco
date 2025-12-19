"""
MCP Input Schemas

Pydantic models for MCP tool input validation.
"""

from pydantic import BaseModel, Field

# =============================================================================
# JOURNAL SCHEMAS
# =============================================================================


class JournalEntryInput(BaseModel):
    """Input for creating a general journal entry."""

    title: str = Field(..., description="Entry title (short description)")
    content: str = Field(..., description="Entry content (detailed text)")
    entry_type: str = Field(
        default="general",
        description="Type: general, task_reflection, decision_log, learning, struggle",
    )
    task_id: str | None = Field(default=None, description="Optional related task")
    tags: list[str] = Field(default_factory=list, description="Optional list of tags")
    is_private: bool = Field(
        default=False, description="If true, only you and CEO/Auditor can see"
    )


class TaskReflectionInput(BaseModel):
    """Input for creating a task reflection entry."""

    task_id: str = Field(..., description="The task UUID you're reflecting on")
    title: str = Field(..., description="Reflection title")
    what_done: str = Field(..., description="What was accomplished")
    what_learned: str = Field(..., description="Key learnings from this task")
    what_struggled: str = Field(..., description="What was difficult or challenging")
    next_steps: list[str] = Field(
        default_factory=list, description="Optional follow-up items"
    )
    tags: list[str] = Field(default_factory=list, description="Optional list of tags")


class DecisionOption(BaseModel):
    """A decision option with pros/cons."""

    name: str = Field(..., description="Option name/title")
    pros: str = Field(default="", description="Pros/advantages of this option")
    cons: str = Field(default="", description="Cons/disadvantages of this option")


class DecisionLogInput(BaseModel):
    """Input for logging a decision."""

    title: str = Field(..., description="Decision title")
    context: str = Field(..., description="What situation led to this decision")
    options: list[DecisionOption] = Field(
        ..., min_length=2, description="Options considered (at least 2)"
    )
    chosen: str = Field(..., description="Which option was chosen")
    rationale: str = Field(..., description="Why this option was chosen")
    consequences: list[str] = Field(
        default_factory=list, description="Expected consequences"
    )
    task_id: str | None = Field(default=None, description="Optional related task")
    tags: list[str] = Field(default_factory=list, description="Optional list of tags")


class LearningInput(BaseModel):
    """Input for logging a learning."""

    title: str = Field(..., description="Learning title")
    what_learned: str = Field(..., description="The actual learning/insight")
    how_applied: str | None = Field(
        default=None, description="How you applied or plan to apply this"
    )
    source: str | None = Field(
        default=None, description="Where you learned this (docs, experiment, etc.)"
    )
    task_id: str | None = Field(default=None, description="Optional related task")
    tags: list[str] = Field(default_factory=list, description="Optional list of tags")


class StruggleInput(BaseModel):
    """Input for logging a struggle."""

    title: str = Field(..., description="Struggle title")
    what_struggled: str = Field(..., description="What the challenge was")
    attempted_solutions: list[str] = Field(
        default_factory=list, description="What you tried (even if it didn't work)"
    )
    resolution: str | None = Field(
        default=None, description="How it was resolved (if resolved)"
    )
    help_needed: str | None = Field(
        default=None, description="What help you need (if unresolved)"
    )
    task_id: str | None = Field(default=None, description="Optional related task")
    tags: list[str] = Field(default_factory=list, description="Optional list of tags")


# =============================================================================
# MESSAGE SCHEMAS
# =============================================================================


class SendMessageInput(BaseModel):
    """Input for sending a message."""

    channel_slug: str = Field(..., description="Channel slug (e.g., 'backend-cell')")
    content: str = Field(..., description="Message content")
    message_type: str = Field(
        default="dialogue",
        description="Type: reasoning, dialogue, decision, action, blocker, technical",
    )
    task_id: str | None = Field(default=None, description="Optional related task ID")
    reply_to: str | None = Field(default=None, description="Message ID to reply to")
    mentions: list[str] = Field(default_factory=list, description="Agents to mention")


class AskQuestionInput(BaseModel):
    """Input for asking a question."""

    channel_slug: str
    question: str
    context: str | None = None
    task_id: str | None = None


class ReportBlockerInput(BaseModel):
    """Input for reporting a blocker."""

    channel_slug: str
    blocker_description: str
    what_needed: str
    task_id: str | None = None


# =============================================================================
# NOTIFICATION SCHEMAS
# =============================================================================


class SendNotificationInput(BaseModel):
    """Input for sending a notification."""

    recipients: list[str] = Field(..., description="Agent IDs to notify")
    subject: str = Field(..., description="Notification subject")
    body: str = Field(..., description="Notification body")
    notification_type: str = Field(
        default="info", description="Type: info, alert, task, escalation, approval"
    )
    priority: str = Field(default="normal", description="low, normal, high, urgent")
    requires_ack: bool = Field(default=True, description="Require acknowledgment")
    related_task_id: str | None = Field(default=None, description="Related task")
