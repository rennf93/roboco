"""
Sessions API Schemas

Request/response models for session endpoints.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from roboco.models import SessionStatus
from roboco.models.session import SessionScope


class ListSessionsParams(BaseModel):
    """Query parameters for listing sessions."""

    group_id: UUID
    status_filter: SessionStatus | None = None
    limit: int = Field(20, ge=1, le=100)


class SessionTaskInfo(BaseModel):
    """Brief task info for session display."""

    task_id: UUID
    task_title: str | None = None
    is_primary: bool
    relationship_type: str


class SessionResponse(BaseModel):
    """Session response."""

    id: UUID
    group_id: UUID
    status: SessionStatus
    scope: SessionScope
    message_count: int
    total_content_length: int
    started_at: datetime
    last_activity_at: datetime
    closed_at: datetime | None
    # Task links for display
    task_links: list[SessionTaskInfo] = Field(default_factory=list)


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


# =============================================================================
# SESSION-TASK SCHEMAS
# =============================================================================


class SessionForTasksCreateRequest(BaseModel):
    """Request to create a session linked to tasks (PM only)."""

    task_ids: list[UUID] = Field(..., min_length=1)
    channel_slug: str
    group_id: UUID | None = Field(
        default=None,
        description="Optional group ID to place session under",
    )
    scope: SessionScope = Field(
        default=SessionScope.CELL,
        description="Scope level: initiative (Main PM), cell (Cell PM), task (dev)",
    )
    relationship_type: str = Field(
        default="discussion",
        description="Type: discussion, planning, review, retrospective",
    )
    max_time_window_minutes: int | None = 30
    max_message_count: int | None = 100
    max_content_length: int | None = 50000
    timeout_seconds: int = 300


class SessionTaskLinkRequest(BaseModel):
    """Request to link a task to a session."""

    task_id: UUID
    is_primary: bool = False
    relationship_type: str = Field(
        default="discussion",
        description="Type: discussion, planning, review, retrospective",
    )


class SessionTaskLinkResponse(BaseModel):
    """Response for a session-task link."""

    id: UUID
    session_id: UUID
    task_id: UUID
    is_primary: bool
    relationship_type: str
    added_at: datetime
    added_by: UUID | None


class SessionTaskLinksResponse(BaseModel):
    """Response containing session with its task links."""

    session: SessionResponse
    links: list[SessionTaskLinkResponse]


class TaskSessionsResponse(BaseModel):
    """Response containing sessions linked to a task."""

    task_id: UUID
    sessions: list[SessionTaskLinkResponse]
    primary_session_id: UUID | None
