"""
Session Model

Sessions group messages within boundaries (time, count, content length).
They are automatically created and closed based on configuration.
"""

from datetime import datetime, timedelta
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
    started_at: datetime = Field(default_factory=datetime.utcnow)
    last_activity_at: datetime = Field(default_factory=datetime.utcnow)
    closed_at: datetime | None = None

    # Statistics
    message_count: int = Field(default=0, ge=0)
    total_content_length: int = Field(default=0, ge=0)

    @property
    def is_active(self) -> bool:
        """Check if session is still active."""
        return self.status == SessionStatus.ACTIVE

    @property
    def duration(self) -> timedelta:
        """Get session duration."""
        end = self.closed_at or datetime.utcnow()
        return end - self.started_at

    def should_close(self) -> bool:
        """Check if any boundary has been reached."""
        now = datetime.utcnow()

        # Check time window
        if self.max_time_window and now - self.started_at >= self.max_time_window:
            return True

        # Check message count
        if self.max_message_count and self.message_count >= self.max_message_count:
            return True

        # Check content length
        if (
            self.max_content_length
            and self.total_content_length >= self.max_content_length
        ):
            return True

        # Check timeout
        if self.timeout_seconds > 0:
            inactivity = (now - self.last_activity_at).total_seconds()
            if inactivity >= self.timeout_seconds:
                return True

        return False

    def add_message(self, content_length: int) -> None:
        """Record a new message in the session."""
        self.message_count += 1
        self.total_content_length += content_length
        self.last_activity_at = datetime.utcnow()

    def close(self, timed_out: bool = False) -> None:
        """Close the session."""
        self.closed_at = datetime.utcnow()
        self.status = SessionStatus.TIMED_OUT if timed_out else SessionStatus.CLOSED


# =============================================================================
# CREATE SCHEMA
# =============================================================================


class SessionCreate(RobocoBase):
    """Schema for creating a new session."""

    group_id: UUID
    config: SessionConfig | None = None
