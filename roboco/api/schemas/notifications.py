"""
Notifications API Schemas

Request/response models for the notification system.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from roboco.models import NotificationPriority, NotificationType


class ListNotificationsParams(BaseModel):
    """Query parameters for listing notifications."""

    unread_only: bool = False
    pending_ack_only: bool = False
    type_filter: NotificationType | None = None
    limit: int = Field(50, ge=1, le=100)


class NotificationResponse(BaseModel):
    """Notification response."""

    id: UUID
    type: NotificationType
    priority: NotificationPriority
    from_agent: UUID
    to_agents: list[UUID]
    subject: str
    body: str
    requires_ack: bool
    is_acknowledged: bool
    is_fully_acknowledged: bool
    is_read: bool
    related_task_id: UUID | None
    timestamp: datetime
    expires_at: datetime | None


class NotificationListResponse(BaseModel):
    """List of notifications."""

    items: list[NotificationResponse]
    total: int
    unread_count: int
    pending_ack_count: int


class NotificationCreateRequest(BaseModel):
    """Request to create a notification."""

    type: NotificationType
    priority: NotificationPriority = NotificationPriority.NORMAL
    to_agents: list[UUID] = Field(..., min_length=1)
    subject: str = Field(..., min_length=1, max_length=200)
    body: str
    requires_ack: bool = True
    related_task_id: UUID | None = None
    expires_at: datetime | None = None
