"""
Notifications API Schemas

Request/response models for the notification system.
"""

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from pydantic import BaseModel, Field

from roboco.models import NotificationPriority, NotificationType
from roboco.utils.converters import require_uuid, to_python_uuid, to_python_uuid_list

if TYPE_CHECKING:
    from roboco.db.tables import NotificationTable


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


# =============================================================================
# RESPONSE CONVERTERS
# =============================================================================


def notification_to_response(
    n: "NotificationTable",
    agent_id: UUID,
) -> NotificationResponse:
    """Convert a notification to response format."""
    return NotificationResponse(
        id=require_uuid(n.id),
        type=n.type,
        priority=n.priority,
        from_agent=require_uuid(n.from_agent),
        to_agents=to_python_uuid_list(n.to_agents),
        subject=n.subject,
        body=n.body,
        requires_ack=n.requires_ack,
        is_acknowledged=agent_id in n.acked_by,
        is_fully_acknowledged=all(a in n.acked_by for a in n.to_agents),
        is_read=agent_id in n.read_by,
        related_task_id=to_python_uuid(n.related_task_id),
        timestamp=n.timestamp,
        expires_at=n.expires_at,
    )
