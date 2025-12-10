"""
Notification Model

Notifications are formal signals that require acknowledgment.
Only PMs, Board members, and the Auditor can send notifications.
"""

from datetime import datetime
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
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime | None = Field(
        default=None, description="Expiration time if applicable"
    )

    # Read tracking (distinct from ACK)
    read_by: list[UUID] = Field(
        default_factory=list, description="Agents who have read the notification"
    )

    @property
    def is_fully_acknowledged(self) -> bool:
        """Check if all recipients have acknowledged."""
        if not self.requires_ack:
            return True
        return all(agent_id in self.acked_by for agent_id in self.to_agents)

    @property
    def pending_acks(self) -> list[UUID]:
        """Get list of agents who haven't acknowledged yet."""
        return [
            agent_id for agent_id in self.to_agents if agent_id not in self.acked_by
        ]

    @property
    def is_expired(self) -> bool:
        """Check if notification has expired."""
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at

    def acknowledge(self, agent_id: UUID) -> bool:
        """
        Acknowledge the notification.

        Returns True if this was a new acknowledgment.
        """
        if agent_id not in self.to_agents:
            return False

        if agent_id in self.acked_by:
            return False  # Already acknowledged

        self.acked_by.append(agent_id)
        self.acked_at[str(agent_id)] = datetime.utcnow()
        return True

    def mark_read(self, agent_id: UUID) -> bool:
        """
        Mark notification as read.

        Returns True if this was a new read.
        """
        if agent_id not in self.to_agents:
            return False

        if agent_id in self.read_by:
            return False

        self.read_by.append(agent_id)
        return True


# =============================================================================
# NOTIFICATION FACTORIES
# =============================================================================


def create_task_assignment(
    from_pm: UUID,
    to_agent: UUID,
    task_id: UUID,
    task_title: str,
    priority: NotificationPriority = NotificationPriority.NORMAL,
) -> Notification:
    """Create a task assignment notification."""
    return Notification(
        type=NotificationType.TASK_ASSIGNMENT,
        priority=priority,
        from_agent=from_pm,
        to_agents=[to_agent],
        subject=f"New Task: {task_title}",
        body=f"You have been assigned a new task: {task_title}",
        related_task_id=task_id,
    )


def create_blocker_escalation(
    from_pm: UUID,
    to_pm: UUID,
    task_id: UUID,
    blocker_description: str,
) -> Notification:
    """Create a blocker escalation notification."""
    return Notification(
        type=NotificationType.BLOCKER_ESCALATION,
        priority=NotificationPriority.HIGH,
        from_agent=from_pm,
        to_agents=[to_pm],
        subject="Blocker Escalation Required",
        body=blocker_description,
        related_task_id=task_id,
    )


def create_review_request(
    from_pm: UUID,
    to_qa: UUID,
    task_id: UUID,
    task_title: str,
) -> Notification:
    """Create a QA review request notification."""
    return Notification(
        type=NotificationType.REVIEW_REQUEST,
        priority=NotificationPriority.NORMAL,
        from_agent=from_pm,
        to_agents=[to_qa],
        subject=f"Review Request: {task_title}",
        body=f"Task '{task_title}' is ready for QA review.",
        related_task_id=task_id,
    )


def create_documentation_request(
    from_pm: UUID,
    to_documenter: UUID,
    task_id: UUID,
    task_title: str,
) -> Notification:
    """Create a documentation request notification."""
    return Notification(
        type=NotificationType.DOCUMENTATION_REQUEST,
        priority=NotificationPriority.NORMAL,
        from_agent=from_pm,
        to_agents=[to_documenter],
        subject=f"Documentation Required: {task_title}",
        body=f"Task '{task_title}' has passed QA and needs documentation.",
        related_task_id=task_id,
    )


def create_priority_change(
    from_agent: UUID,
    to_agents: list[UUID],
    task_id: UUID,
    task_title: str,
    new_priority: int,
) -> Notification:
    """Create a priority change notification."""
    priority_labels = {
        0: "P0 (Critical)",
        1: "P1 (High)",
        2: "P2 (Medium)",
        3: "P3 (Low)",
    }
    return Notification(
        type=NotificationType.PRIORITY_CHANGE,
        priority=NotificationPriority.URGENT
        if new_priority == 0
        else NotificationPriority.HIGH,
        from_agent=from_agent,
        to_agents=to_agents,
        subject=f"Priority Changed: {task_title}",
        body=f"Task '{task_title}' priority changed to {priority_labels.get(new_priority, f'P{new_priority}')}",
        related_task_id=task_id,
    )


def create_alert(
    from_agent: UUID,
    to_agents: list[UUID],
    subject: str,
    body: str,
) -> Notification:
    """Create an urgent alert notification."""
    return Notification(
        type=NotificationType.ALERT,
        priority=NotificationPriority.URGENT,
        from_agent=from_agent,
        to_agents=to_agents,
        subject=subject,
        body=body,
    )


def create_broadcast(
    from_agent: UUID,
    to_agents: list[UUID],
    subject: str,
    body: str,
) -> Notification:
    """Create a broadcast notification (requires read confirmation, not ACK)."""
    return Notification(
        type=NotificationType.BROADCAST,
        priority=NotificationPriority.NORMAL,
        from_agent=from_agent,
        to_agents=to_agents,
        subject=subject,
        body=body,
        requires_ack=False,  # Broadcasts just need to be read
    )


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
