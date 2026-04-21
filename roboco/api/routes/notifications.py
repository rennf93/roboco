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
    NotificationCreateRequest,
    NotificationListResponse,
    NotificationResponse,
    notification_to_response,
)
from roboco.enforcement import NotificationPermissionError
from roboco.services.base import NotFoundError
from roboco.services.notification_delivery import (
    ApiNotificationCreate,
    get_notification_delivery_service,
)

router = APIRouter()

_MIN_SUBJECT_CHARS = 5
_MIN_BODY_CHARS = 10
_MAX_RECIPIENTS = 50


def _assert_notification_content(data: NotificationCreateRequest) -> None:
    """Validate subject/body/recipients gates for send_notification."""
    if not data.subject or len(data.subject.strip()) < _MIN_SUBJECT_CHARS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"SUBJECT_REQUIRED: Notification subject must be >= "
                f"{_MIN_SUBJECT_CHARS} chars."
            ),
        )
    if not data.body or len(data.body.strip()) < _MIN_BODY_CHARS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"BODY_REQUIRED: Notification body must be >= "
                f"{_MIN_BODY_CHARS} chars. Say what to do next."
            ),
        )
    if len(data.to_agents) > _MAX_RECIPIENTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"TOO_MANY_RECIPIENTS: {len(data.to_agents)} recipients "
                f"exceeds {_MAX_RECIPIENTS}. Post in a broadcast channel "
                "instead of spraying notifications."
            ),
        )


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
    """Send a notification; delegates recipient lookup + permission check."""
    _assert_notification_content(data)

    service = get_notification_delivery_service(db)
    try:
        notification = await service.send_from_api(
            sender_agent_id=agent_id,
            data=ApiNotificationCreate(
                type=data.type,
                priority=data.priority,
                to_agents=list(data.to_agents),
                subject=data.subject,
                body=data.body,
                requires_ack=data.requires_ack,
                related_task_id=data.related_task_id,
                expires_at=data.expires_at,
            ),
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except NotificationPermissionError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=e.message
        ) from e

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


@router.get(
    "/pending-a2a",
    summary="Check pending A2A",
    description="Check if there's a pending A2A notification to a target about a task.",
)
async def check_pending_a2a(
    db: DbSession,
    from_agent: str,
    to_agent: str,
    task_id: str,
) -> dict[str, bool]:
    """
    Check if there's already a pending A2A notification.

    Prevents duplicate messages — one message per task until response. The
    route resolves slugs → UUIDs (pure lookup against AGENT_UUIDS config)
    then hands the check to the service.
    """
    from roboco.seeds.initial_data import AGENT_UUIDS

    from_uuid = AGENT_UUIDS.get(from_agent)
    to_uuid = AGENT_UUIDS.get(to_agent)
    if not from_uuid or not to_uuid:
        return {"has_pending": False}

    try:
        task_uuid = UUID(task_id)
    except ValueError:
        return {"has_pending": False}

    service = get_notification_delivery_service(db)
    has_pending = await service.has_pending_a2a(
        from_agent_id=UUID(from_uuid),
        to_agent_id=UUID(to_uuid),
        task_id=task_uuid,
    )
    return {"has_pending": has_pending}


@router.post(
    "/ack-a2a",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Auto-ack A2A notifications",
    description="Acknowledge A2A notifications when responding. Called by SDK.",
)
async def ack_a2a_notifications(
    db: DbSession,
    data: dict[str, str],
) -> None:
    """Auto-ack A2A_REQUEST notifications when agent B responds to agent A."""
    from roboco.seeds.initial_data import AGENT_UUIDS

    from_uuid = AGENT_UUIDS.get(data.get("from_agent", ""))
    to_uuid = AGENT_UUIDS.get(data.get("to_agent", ""))
    task_id_str = data.get("task_id", "")
    if not from_uuid or not to_uuid or not task_id_str:
        return
    try:
        task_uuid = UUID(task_id_str)
    except ValueError:
        return

    service = get_notification_delivery_service(db)
    await service.auto_ack_a2a(
        from_agent_id=UUID(from_uuid),
        to_agent_id=UUID(to_uuid),
        task_id=task_uuid,
    )
