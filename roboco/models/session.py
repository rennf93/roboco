"""
Session Model

Sessions group messages within boundaries (time, count, content length).
They are automatically created and closed based on configuration.

Session-Task Relationships:
    PMs can create work sessions as discussion contexts for tasks.
    A session can discuss multiple related tasks.
    A task can have multiple sessions (planning, review, retrospective).
"""

from datetime import UTC, datetime, timedelta
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import Field

from roboco.models.base import (
    RobocoBase,
    SessionStatus,
    TimestampMixin,
)

# =============================================================================
# SESSION SCOPE (Context Level)
# =============================================================================


class SessionScope(StrEnum):
    """
    Scope level for sessions - determines context loading strategy.

    Sessions at different scopes serve different purposes:
    - INITIATIVE: Cross-cell coordination (Main PM, #dev-all)
    - CELL: Cell-specific work (Cell PM, #backend-cell)
    - TASK: Individual task execution (Developer level)

    When loading context for an agent:
    - Load their scope's sessions fully
    - Load parent scope sessions as summaries/references
    """

    INITIATIVE = "initiative"  # Cross-cell, Main PM level
    CELL = "cell"  # Cell-specific, Cell PM level
    TASK = "task"  # Individual task execution


# =============================================================================
# SESSION-TASK RELATIONSHIP TYPES
# =============================================================================


class SessionTaskRelationshipType(StrEnum):
    """Type of relationship between a session and a task."""

    DISCUSSION = "discussion"  # General discussion about the task
    PLANNING = "planning"  # Planning session for the task
    REVIEW = "review"  # Review/retrospective session
    RETROSPECTIVE = "retrospective"  # Post-completion reflection


# =============================================================================
# SUPPORTING MODELS
# =============================================================================


class SessionConfig(RobocoBase):
    """Configuration for session boundaries."""

    max_time_window: timedelta | None = Field(
        default=timedelta(minutes=30), description="Maximum session duration"
    )
    max_message_count: int | None = Field(
        default=100, ge=1, description="Maximum messages per session"
    )
    max_content_length: int | None = Field(
        default=50000, ge=1, description="Maximum total characters per session"
    )
    timeout_seconds: int = Field(
        default=300, ge=0, description="Inactivity timeout in seconds"
    )


# =============================================================================
# MAIN SESSION MODEL
# =============================================================================


class Session(TimestampMixin):
    """
    A session groups messages within boundaries.

    Sessions can be bounded by time, message count, or content length.
    Any boundary being reached triggers session closure.
    """

    # Identity
    id: UUID = Field(default_factory=uuid4, description="Session ID (sesh_id)")
    group_id: UUID = Field(..., description="Parent group ID")

    # Boundaries
    max_time_window: timedelta | None = Field(
        default=timedelta(minutes=30), description="Maximum session duration"
    )
    max_message_count: int | None = Field(
        default=100, ge=1, description="Maximum messages per session"
    )
    max_content_length: int | None = Field(
        default=50000, ge=1, description="Maximum total characters"
    )

    # Timeout
    timeout_seconds: int = Field(default=300, ge=0, description="Inactivity timeout")

    # State
    status: SessionStatus = Field(default=SessionStatus.ACTIVE)

    # Scope (for smart context loading)
    scope: SessionScope = Field(
        default=SessionScope.TASK,
        description="Session scope level - initiative, cell, or task",
    )

    # Timestamps
    started_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    last_activity_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    closed_at: datetime | None = None

    # Statistics
    message_count: int = Field(default=0, ge=0)
    total_content_length: int = Field(default=0, ge=0)

    # NOTE: Session state mutations and boundary checks should be performed
    # through a SessionService. Methods like should_close, add_message, close
    # should be in a service layer. The is_active check is a simple comparison:
    # session.status == SessionStatus.ACTIVE


# =============================================================================
# CREATE SCHEMA
# =============================================================================


class SessionCreate(RobocoBase):
    """Schema for creating a new session."""

    group_id: UUID
    config: SessionConfig | None = None


# =============================================================================
# SESSION-TASK LINK MODELS
# =============================================================================


class SessionTaskLink(TimestampMixin):
    """
    Represents a link between a session and a task.

    Used for reading/displaying session-task relationships.
    """

    id: UUID = Field(default_factory=uuid4, description="Link ID")
    session_id: UUID = Field(..., description="Session ID")
    task_id: UUID = Field(..., description="Task ID")

    # Relationship metadata
    is_primary: bool = Field(
        default=False, description="Is this the primary discussion session for the task"
    )
    relationship_type: SessionTaskRelationshipType = Field(
        default=SessionTaskRelationshipType.DISCUSSION,
        description="Type of session-task relationship",
    )

    # Audit
    added_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    added_by: UUID | None = Field(default=None, description="PM who created the link")


class SessionTaskLinkCreate(RobocoBase):
    """Schema for creating a session-task link."""

    session_id: UUID = Field(..., description="Session to link")
    task_id: UUID = Field(..., description="Task to link")
    is_primary: bool = Field(
        default=False, description="Mark as primary session for this task"
    )
    relationship_type: SessionTaskRelationshipType = Field(
        default=SessionTaskRelationshipType.DISCUSSION,
        description="Type of relationship",
    )


class SessionForTasksCreate(RobocoBase):
    """Schema for PM creating a session linked to multiple tasks."""

    task_ids: list[UUID] = Field(..., min_length=1, description="Tasks to link")
    channel_slug: str = Field(..., description="Channel where session is created")
    scope: SessionScope = Field(
        default=SessionScope.CELL,
        description="Session scope level for context loading strategy",
    )
    config: SessionConfig | None = Field(
        default=None, description="Session boundary configuration"
    )
    relationship_type: SessionTaskRelationshipType = Field(
        default=SessionTaskRelationshipType.DISCUSSION,
        description="Relationship type for all links",
    )
