"""
Sessions API Schemas

Request/response models for session endpoints.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from roboco.models import SessionStatus


class ListSessionsParams(BaseModel):
    """Query parameters for listing sessions."""

    group_id: UUID
    status_filter: SessionStatus | None = None
    limit: int = Field(20, ge=1, le=100)


class SessionResponse(BaseModel):
    """Session response."""

    id: UUID
    group_id: UUID
    status: SessionStatus
    message_count: int
    total_content_length: int
    started_at: datetime
    last_activity_at: datetime
    closed_at: datetime | None


class SessionListResponse(BaseModel):
    """List of sessions."""

    items: list[SessionResponse]
    total: int


class SessionCreateRequest(BaseModel):
    """Request to create a session."""

    group_id: UUID
    max_time_window_minutes: int | None = 30
    max_message_count: int | None = 100
    max_content_length: int | None = 50000
    timeout_seconds: int = 300
