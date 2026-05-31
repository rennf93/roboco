"""
Notification Model

Notifications are formal signals that require acknowledgment.
Only PMs, Board members, and the Auditor can send notifications.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from pydantic import Field

from roboco.models.base import (
    NotificationPriority,
    NotificationType,
    RobocoBase,
    TimestampMixin,
)

# =============================================================================
# MAIN NOTIFICATION MODEL
# =============================================================================


class Notification(TimestampMixin):
    """
    Formal signal requiring acknowledgment.

    Notifications are different from communication - they are
    explicit signals sent through proper channels.
    """

    # Identity
    id: UUID = Field(default_factory=uuid4, description="Notification ID")
    type: NotificationType = Field(..., description="Type of notification")
    priority: NotificationPriority = Field(default=NotificationPriority.NORMAL)

    # Routing
    from_agent: UUID = Field(..., description="Sender (must be PM/Board/Auditor)")
    to_agents: list[UUID] = Field(..., min_length=1, description="Recipient agent IDs")

    # Content
    subject: str = Field(..., min_length=1, max_length=200, description="Subject line")
    body: str = Field(..., description="Notification body")

    # Acknowledgment
    requires_ack: bool = Field(default=True, description="Whether ACK is required")
    acked_by: list[UUID] = Field(
        default_factory=list, description="Agents who have acknowledged"
    )
    acked_at: dict[str, datetime] = Field(
        default_factory=dict,
        description="When each agent acknowledged (agent_id string -> datetime)",
    )

    # Context
    related_task_id: UUID | None = Field(
        default=None, description="Related task if applicable"
    )
    related_message_ids: list[UUID] = Field(
        default_factory=list, description="Related message IDs"
    )

    # Timing
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    expires_at: datetime | None = Field(
        default=None, description="Expiration time if applicable"
    )

    # Read tracking (distinct from ACK)
    read_by: list[UUID] = Field(
        default_factory=list, description="Agents who have read the notification"
    )

    # NOTE: Notification state mutations should be performed through
    # NotificationService. Methods like acknowledge, mark_read should be
    # in a service. Status checks (is_fully_acknowledged, pending_acks,
    # is_expired) should also be in the service layer.


# =============================================================================
# CREATE SCHEMA
# =============================================================================


class NotificationCreate(RobocoBase):
    """Schema for creating a new notification."""

    type: NotificationType
    priority: NotificationPriority = NotificationPriority.NORMAL
    from_agent: UUID
    to_agents: list[UUID] = Field(..., min_length=1)
    subject: str = Field(..., min_length=1, max_length=200)
    body: str
    requires_ack: bool = True
    related_task_id: UUID | None = None
    related_message_ids: list[UUID] = Field(default_factory=list)
    expires_at: datetime | None = None


# =============================================================================
# SERVICE PARAMETERS
# =============================================================================


@dataclass
class CreateNotificationParams:
    """Parameters for creating a notification via NotificationService."""

    notification_type: NotificationType
    priority: NotificationPriority
    from_agent: str
    to_agents: list[str]
    subject: str
    body: str
    related_task_id: str | None = None
