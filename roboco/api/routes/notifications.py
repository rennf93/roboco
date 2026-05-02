"""
Notification Routes

Formal notification system for PMs, Board, and Auditor. Routes are thin:
validate HTTP input, call NotificationDeliveryService, convert service
exceptions to HTTP status codes. All DB access lives in the service.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from roboco.api.deps import CurrentAgentContext, CurrentAgentId, DbSession
from roboco.api.schemas.notifications import (
    ListNotificationsParams,
    NotificationListResponse,
    NotificationResponse,
    notification_to_response,
)
from roboco.services.base import NotFoundError
from roboco.services.notification_delivery import (
    get_notification_delivery_service,
)

router = APIRouter()


@router.get(
    "",
    response_model=NotificationListResponse,
    summary="List notifications",
    description="List notifications for the current agent.",
)
async def list_notifications(
    db: DbSession,
    agent: CurrentAgentContext,
    params: Annotated[ListNotificationsParams, Depends()],
) -> NotificationListResponse:
    """List notifications for the agent.

    System role (orchestrator) sees everything by type; regular agents
    only see notifications where they are a target.
    """
    service = get_notification_delivery_service(db)
    agent_id = agent.agent_id

    if agent.role and agent.role.value == "system":
        notifications = await service.list_system_notifications(
            pending_ack_only=params.pending_ack_only,
            type_filter=params.type_filter,
            limit=params.limit,
        )
    else:
        notifications = await service.list_for_agent(
            agent_id=agent_id,
            unread_only=params.unread_only,
            pending_ack_only=params.pending_ack_only,
            type_filter=params.type_filter,
            limit=params.limit,
        )

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
    """Get a notification and auto-mark it read for this recipient."""
    service = get_notification_delivery_service(db)
    try:
        notification = await service.get_for_recipient_and_mark_read(
            notification_id=notification_id, agent_id=agent_id
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
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
    """Acknowledge a notification that requires it."""
    service = get_notification_delivery_service(db)
    try:
        notification = await service.acknowledge_for_recipient(
            notification_id=notification_id, agent_id=agent_id
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)
        ) from e
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
    """Mark a notification as read (idempotent)."""
    service = get_notification_delivery_service(db)
    try:
        await service.mark_read_for_recipient(
            notification_id=notification_id, agent_id=agent_id
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except PermissionError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
