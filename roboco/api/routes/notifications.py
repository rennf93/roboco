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

from roboco.api.deps import CurrentAgentContext, CurrentAgentId, DbSession
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
    agent: CurrentAgentContext,
    params: Annotated[ListNotificationsParams, Depends()],
) -> NotificationListResponse:
    """List notifications for the agent.

    System role (orchestrator) can see ALL notifications by type.
    Regular agents only see notifications where they are a target.
    """
    agent_id = agent.agent_id

    # System role (orchestrator) bypasses to_agents filter
    # This allows the dispatcher to query all pending escalations/a2a/etc.
    if agent.role and agent.role.value == "system":
        # Build query without to_agents filter
        query = select(NotificationTable)
        if params.pending_ack_only:
            # For system, "pending" means has at least one target not yet acked
            # We can't easily express "not fully acked" in SQL, so we filter
            # after fetching. But we can at least require requires_ack=True.
            query = query.where(NotificationTable.requires_ack.is_(True))
        if params.type_filter:
            query = query.where(NotificationTable.type == params.type_filter)
        query = query.order_by(NotificationTable.timestamp.desc()).limit(params.limit)

        # Execute and filter out fully acknowledged
        result_all: Any = await db.execute(query)
        all_notifications = result_all.scalars().all()
        if params.pending_ack_only:
            # Filter to only notifications not fully acknowledged
            notifications = [
                n
                for n in all_notifications
                if not all(t in n.acked_by for t in n.to_agents)
            ]
        else:
            notifications = list(all_notifications)
    else:
        # Normal query - filter by to_agents
        query = build_notification_query(NotificationTable, agent_id, params)
        result: Any = await db.execute(query)
        notifications = list(result.scalars().all())

    # For system role, use a dummy agent_id for response formatting
    response_agent_id = agent_id

    unread_count = sum(1 for n in notifications if response_agent_id not in n.read_by)
    pending_ack_count = sum(
        1
        for n in notifications
        if n.requires_ack and response_agent_id not in n.acked_by
    )
    items = [notification_to_response(n, response_agent_id) for n in notifications]

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

    Prevents duplicate messages - one message per task until response.
    """
    from roboco.models.base import NotificationType
    from roboco.seeds.initial_data import AGENT_UUIDS

    from_uuid = AGENT_UUIDS.get(from_agent)
    to_uuid = AGENT_UUIDS.get(to_agent)

    if not from_uuid or not to_uuid:
        return {"has_pending": False}

    # Validate task_id is a valid UUID
    try:
        task_uuid = UUID(task_id)
    except ValueError:
        return {"has_pending": False}

    # Check for unacked A2A_REQUEST from this agent to target about this task
    result = await db.execute(
        select(NotificationTable).where(
            NotificationTable.type == NotificationType.A2A_REQUEST,
            NotificationTable.from_agent == UUID(from_uuid),
            NotificationTable.related_task_id == task_uuid,
            NotificationTable.to_agents.contains([UUID(to_uuid)]),
        )
    )
    notifications = result.scalars().all()

    # Check if any are unacked by the target
    to_uuid_obj = UUID(to_uuid)
    for notif in notifications:
        if to_uuid_obj not in notif.acked_by:
            return {"has_pending": True}

    return {"has_pending": False}


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
    """
    Auto-acknowledge A2A notifications when responding.

    When agent B responds to agent A about a task, this acks any pending
    A2A_REQUEST notifications from A to B about that task.

    Body: {from_agent, to_agent, task_id}
    """
    from roboco.models.base import NotificationType
    from roboco.seeds.initial_data import AGENT_UUIDS

    from_agent_slug = data.get("from_agent", "")
    to_agent_slug = data.get("to_agent", "")
    task_id_str = data.get("task_id", "")

    # Get UUIDs from slugs
    from_agent_uuid = AGENT_UUIDS.get(from_agent_slug)
    to_agent_uuid = AGENT_UUIDS.get(to_agent_slug)

    if not from_agent_uuid or not to_agent_uuid or not task_id_str:
        return  # Silently ignore invalid data

    # Validate task_id is a valid UUID
    try:
        task_uuid = UUID(task_id_str)
    except ValueError:
        return  # Invalid task ID

    # Find matching A2A_REQUEST notifications
    result = await db.execute(
        select(NotificationTable).where(
            NotificationTable.type == NotificationType.A2A_REQUEST,
            NotificationTable.from_agent == UUID(from_agent_uuid),
            NotificationTable.related_task_id == task_uuid,
            NotificationTable.to_agents.contains([UUID(to_agent_uuid)]),
        )
    )
    notifications = result.scalars().all()

    # Acknowledge each matching notification
    to_uuid = UUID(to_agent_uuid)
    now = datetime.now(UTC).isoformat()

    for notif in notifications:
        if to_uuid not in notif.acked_by:
            notif.acked_by = [*notif.acked_by, to_uuid]
            notif.acked_at = {**notif.acked_at, str(to_uuid): now}
        if to_uuid not in notif.read_by:
            notif.read_by = [*notif.read_by, to_uuid]

    await db.flush()
