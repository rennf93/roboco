"""
Session Model

Sessions group messages within boundaries (time, count, content length).
They are automatically created and closed based on configuration.
"""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from pydantic import Field

from roboco.models.base import (
    RobocoBase,
    SessionStatus,
    TimestampMixin,
)

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
