"""
Notification Routes

Formal notification system for PMs, Board, and Auditor.
Enforces permission rules: only PMs, Board, and Auditor can send notifications.
"""

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from roboco.api.deps import CurrentAgentId, DbSession
from roboco.db.tables import AgentTable, NotificationTable
from roboco.enforcement import (
    NotificationPermissionError,
    validate_notification_permission,
)
from roboco.models import NotificationPriority, NotificationType
from roboco.utils.converters import require_uuid, to_python_uuid, to_python_uuid_list

router = APIRouter()


# =============================================================================
# Query Parameter Models
# =============================================================================


class ListNotificationsParams(BaseModel):
    """Query parameters for listing notifications."""

    unread_only: bool = False
    pending_ack_only: bool = False
    type_filter: NotificationType | None = None
    limit: int = Field(50, ge=1, le=100)


# =============================================================================
# Response Models
# =============================================================================


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
# Routes
# =============================================================================


def _build_notification_query(
    agent_id: UUID,
    params: ListNotificationsParams,
) -> Any:
    """Build the notification query with filters."""
    query = select(NotificationTable).where(
        NotificationTable.to_agents.contains([agent_id])
    )
    if params.unread_only:
        query = query.where(~NotificationTable.read_by.contains([agent_id]))
    if params.pending_ack_only:
        query = query.where(
            NotificationTable.requires_ack.is_(True),
            ~NotificationTable.acked_by.contains([agent_id]),
        )
    if params.type_filter:
        query = query.where(NotificationTable.type == params.type_filter)
    return query.order_by(NotificationTable.timestamp.desc()).limit(params.limit)


def _notification_to_response(
    n: NotificationTable,
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


@router.get(
    "",
    response_model=NotificationListResponse,
    summary="List notifications",
    description="List notifications for the current agent.",
)
async def list_notifications(
    db: DbSession,
    agent_id: CurrentAgentId,
    params: Annotated[ListNotificationsParams, Depends()],
) -> NotificationListResponse:
    """List notifications for the agent."""
    query = _build_notification_query(agent_id, params)
    result: Any = await db.execute(query)
    notifications = result.scalars().all()

    unread_count = sum(1 for n in notifications if agent_id not in n.read_by)
    pending_ack_count = sum(
        1 for n in notifications if n.requires_ack and agent_id not in n.acked_by
    )
    items = [_notification_to_response(n, agent_id) for n in notifications]

    return NotificationListResponse(
        items=items,
        total=len(items),
        unread_count=unread_count,
        pending_ack_count=pending_ack_count,
    )


@router.get(
    "/{notification_id}",
    response_model=NotificationResponse,
    summary="Get notification",
    description="Get a specific notification.",
)
async def get_notification(
    db: DbSession,
    agent_id: CurrentAgentId,
    notification_id: UUID,
) -> NotificationResponse:
    """Get a notification."""
    result = await db.execute(
        select(NotificationTable).where(NotificationTable.id == notification_id)
    )
    notification = result.scalar_one_or_none()

    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )

    # Check if agent is a recipient
    if agent_id not in notification.to_agents:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a recipient of this notification",
        )

    # Mark as read
    if agent_id not in notification.read_by:
        notification.read_by = [*notification.read_by, agent_id]
        await db.flush()

    return NotificationResponse(
        id=require_uuid(notification.id),
        type=notification.type,
        priority=notification.priority,
        from_agent=require_uuid(notification.from_agent),
        to_agents=to_python_uuid_list(notification.to_agents),
        subject=notification.subject,
        body=notification.body,
        requires_ack=notification.requires_ack,
        is_acknowledged=agent_id in notification.acked_by,
        is_fully_acknowledged=all(
            a in notification.acked_by for a in notification.to_agents
        ),
        is_read=True,
        related_task_id=to_python_uuid(notification.related_task_id),
        timestamp=notification.timestamp,
        expires_at=notification.expires_at,
    )


@router.post(
    "",
    response_model=NotificationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Send notification",
    description="Send a notification. Only PMs, Board, and Auditor can send.",
)
async def send_notification(
    db: DbSession,
    agent_id: CurrentAgentId,
    data: NotificationCreateRequest,
) -> NotificationResponse:
    """
    Send a notification.

    Enforces permission rules:
    - Only PMs, Board members, and Auditor can send notifications
    - Cell PMs can only notify members of their own cell
    - Main PM, Auditor, and CEO can notify anyone
    """
    # Look up the sending agent to get their agent_id string
    agent_result = await db.execute(select(AgentTable).where(AgentTable.id == agent_id))
    agent = agent_result.scalar_one_or_none()

    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found",
        )

    # Look up recipient agent_ids
    recipient_ids = []
    for recipient_uuid in data.to_agents:
        recipient_result = await db.execute(
            select(AgentTable).where(AgentTable.id == recipient_uuid)
        )
        recipient = recipient_result.scalar_one_or_none()
        if recipient:
            recipient_ids.append(str(recipient.id))

    # Validate notification permissions using enforcement layer
    try:
        validate_notification_permission(
            sender_id=agent.slug,
            recipients=recipient_ids,
        )
    except NotificationPermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=e.message,
        ) from e

    notification = NotificationTable(
        type=data.type,
        priority=data.priority,
        from_agent=agent_id,
        to_agents=data.to_agents,
        subject=data.subject,
        body=data.body,
        requires_ack=data.requires_ack,
        related_task_id=data.related_task_id,
        expires_at=data.expires_at,
    )

    db.add(notification)
    await db.flush()

    return NotificationResponse(
        id=require_uuid(notification.id),
        type=notification.type,
        priority=notification.priority,
        from_agent=require_uuid(notification.from_agent),
        to_agents=to_python_uuid_list(notification.to_agents),
        subject=notification.subject,
        body=notification.body,
        requires_ack=notification.requires_ack,
        is_acknowledged=False,
        is_fully_acknowledged=False,
        is_read=False,
        related_task_id=to_python_uuid(notification.related_task_id),
        timestamp=notification.timestamp,
        expires_at=notification.expires_at,
    )


@router.post(
    "/{notification_id}/ack",
    response_model=NotificationResponse,
    summary="Acknowledge notification",
    description="Acknowledge a notification.",
)
async def acknowledge_notification(
    db: DbSession,
    agent_id: CurrentAgentId,
    notification_id: UUID,
) -> NotificationResponse:
    """Acknowledge a notification."""
    result = await db.execute(
        select(NotificationTable).where(NotificationTable.id == notification_id)
    )
    notification = result.scalar_one_or_none()

    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )

    # Check if agent is a recipient
    if agent_id not in notification.to_agents:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a recipient of this notification",
        )

    if not notification.requires_ack:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This notification does not require acknowledgment",
        )

    # Add acknowledgment
    if agent_id not in notification.acked_by:
        notification.acked_by = [*notification.acked_by, agent_id]
        notification.acked_at = {
            **notification.acked_at,
            str(agent_id): datetime.now(UTC).isoformat(),
        }

    # Also mark as read
    if agent_id not in notification.read_by:
        notification.read_by = [*notification.read_by, agent_id]

    await db.flush()

    return NotificationResponse(
        id=require_uuid(notification.id),
        type=notification.type,
        priority=notification.priority,
        from_agent=require_uuid(notification.from_agent),
        to_agents=to_python_uuid_list(notification.to_agents),
        subject=notification.subject,
        body=notification.body,
        requires_ack=notification.requires_ack,
        is_acknowledged=True,
        is_fully_acknowledged=all(
            a in notification.acked_by for a in notification.to_agents
        ),
        is_read=True,
        related_task_id=to_python_uuid(notification.related_task_id),
        timestamp=notification.timestamp,
        expires_at=notification.expires_at,
    )


@router.post(
    "/{notification_id}/read",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Mark as read",
    description="Mark a notification as read.",
)
async def mark_as_read(
    db: DbSession,
    agent_id: CurrentAgentId,
    notification_id: UUID,
) -> None:
    """Mark a notification as read."""
    result = await db.execute(
        select(NotificationTable).where(NotificationTable.id == notification_id)
    )
    notification = result.scalar_one_or_none()

    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found",
        )

    if agent_id not in notification.to_agents:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not a recipient of this notification",
        )

    if agent_id not in notification.read_by:
        notification.read_by = [*notification.read_by, agent_id]
        await db.flush()
