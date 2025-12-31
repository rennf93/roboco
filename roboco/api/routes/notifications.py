"""
Notification Routes

Formal notification system for PMs, Board, and Auditor.
Enforces permission rules: only PMs, Board, and Auditor can send notifications.
"""

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from roboco.api.deps import CurrentAgentId, DbSession
from roboco.api.schemas.notifications import (
    ListNotificationsParams,
    NotificationCreateRequest,
    NotificationListResponse,
    NotificationResponse,
    build_notification_query,
    notification_to_response,
)
from roboco.api.utils import get_or_404, require_recipient
from roboco.db.tables import AgentTable, NotificationTable
from roboco.enforcement import (
    NotificationPermissionError,
    validate_notification_permission,
)
from roboco.services.notification_delivery import get_notification_delivery_service
from roboco.utils.converters import require_uuid

router = APIRouter()


# =============================================================================
# Routes
# =============================================================================


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
    query = build_notification_query(NotificationTable, agent_id, params)
    result: Any = await db.execute(query)
    notifications = result.scalars().all()

    unread_count = sum(1 for n in notifications if agent_id not in n.read_by)
    pending_ack_count = sum(
        1 for n in notifications if n.requires_ack and agent_id not in n.acked_by
    )
    items = [notification_to_response(n, agent_id) for n in notifications]

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
    notification = await get_or_404(
        db, NotificationTable, notification_id, "Notification"
    )
    require_recipient(notification.to_agents, agent_id, "view notification")

    # Mark as read
    if agent_id not in notification.read_by:
        notification.read_by = [*notification.read_by, agent_id]
        await db.flush()

    return notification_to_response(notification, agent_id)


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
    - Cell PMs can notify their cell members, Main PM, or other Cell PMs
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

    # Look up recipient slugs for permission checking
    recipient_slugs = []
    for recipient_uuid in data.to_agents:
        recipient_result = await db.execute(
            select(AgentTable).where(AgentTable.id == recipient_uuid)
        )
        recipient = recipient_result.scalar_one_or_none()
        if recipient:
            recipient_slugs.append(recipient.slug)

    # Validate notification permissions using enforcement layer
    try:
        validate_notification_permission(
            sender_id=agent.slug,
            recipients=recipient_slugs,
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

    # Deliver notification via Redis Streams for real-time push
    delivery_service = get_notification_delivery_service(db)
    await delivery_service.deliver(require_uuid(notification.id))

    return notification_to_response(notification, agent_id)


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
    notification = await get_or_404(
        db, NotificationTable, notification_id, "Notification"
    )
    require_recipient(notification.to_agents, agent_id, "acknowledge notification")

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
    return notification_to_response(notification, agent_id)


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
    notification = await get_or_404(
        db, NotificationTable, notification_id, "Notification"
    )
    require_recipient(notification.to_agents, agent_id, "mark notification read")

    if agent_id not in notification.read_by:
        notification.read_by = [*notification.read_by, agent_id]
        await db.flush()
