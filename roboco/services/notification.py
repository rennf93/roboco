"""
Notification Service

Sends notifications through the API with proper enforcement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import structlog
from sqlalchemy import select

from roboco.db.base import get_db_context
from roboco.db.tables import AgentTable, NotificationTable
from roboco.models import NotificationPriority, NotificationType
from roboco.models.notification import CreateNotificationParams
from roboco.utils.converters import require_uuid

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()


async def _resolve_agent_uuid(
    db: AsyncSession, value: str | UUID | None
) -> UUID | None:
    """Turn an agent slug or UUID (any case / any form) into a real UUID.

    `notifications.from_agent` is UUID-typed in the DB + FK to agents.id.
    Callers across the codebase pass slugs ("be-doc", "system", etc.) —
    this resolver does the slug→UUID translation. "system" resolves to
    the seeded system agent (stable UUID) so orchestrator-generated
    notifications always have a valid sender.

    Returns None only for truly absent values (None, empty string, or a
    slug we can't find). The caller in `_create_notification` logs +
    skips in that case rather than crashing on FK violation.
    """
    if value is None or value == "":
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except ValueError:
        pass
    result = await db.execute(select(AgentTable).where(AgentTable.slug == str(value)))
    agent = result.scalar_one_or_none()
    return UUID(str(agent.id)) if agent else None


class NotificationService:
    """Service for sending system-generated notifications."""

    async def send_blocker_notification(
        self,
        task_id: str,
        blocker_reason: str,
        from_agent: str | None,
        to_pm: str,
    ) -> None:
        """Send notification about a blocked task."""
        logger.info(
            "Sending blocker notification",
            task_id=task_id,
            to_pm=to_pm,
        )

        # System notifications bypass normal permission checks
        body = (
            f"Task {task_id} has been blocked.\n\n"
            f"Reason: {blocker_reason}\n\n"
            "Please investigate and help resolve."
        )
        await self._create_notification(
            CreateNotificationParams(
                notification_type=NotificationType.BLOCKER_ESCALATION,
                priority=NotificationPriority.HIGH,
                from_agent=from_agent or "system",
                to_agents=[to_pm],
                subject=f"Task {task_id} is blocked",
                body=body,
                related_task_id=task_id,
            )
        )

    async def send_qa_ready_notification(
        self,
        task_id: str,
        from_agent: str | None,
        to_qa: str,
    ) -> None:
        """Send notification that task is ready for QA."""
        logger.info(
            "Sending QA ready notification",
            task_id=task_id,
            to_qa=to_qa,
        )

        body = (
            f"Task {task_id} is ready for QA review.\n\n"
            "Please review and provide feedback."
        )
        await self._create_notification(
            CreateNotificationParams(
                notification_type=NotificationType.REVIEW_REQUEST,
                priority=NotificationPriority.NORMAL,
                from_agent=from_agent or "system",
                to_agents=[to_qa],
                subject=f"Task {task_id} ready for QA",
                body=body,
                related_task_id=task_id,
            )
        )

    async def send_docs_ready_notification(
        self,
        task_id: str,
        from_agent: str | None,
        to_documenter: str,
    ) -> None:
        """Send notification that task is ready for documentation."""
        logger.info(
            "Sending docs ready notification",
            task_id=task_id,
            to_documenter=to_documenter,
        )

        body = (
            f"Task {task_id} has passed QA and is ready for documentation.\n\n"
            "Please create the required documentation."
        )
        await self._create_notification(
            CreateNotificationParams(
                notification_type=NotificationType.DOCUMENTATION_REQUEST,
                priority=NotificationPriority.NORMAL,
                from_agent=from_agent or "system",
                to_agents=[to_documenter],
                subject=f"Task {task_id} needs documentation",
                body=body,
                related_task_id=task_id,
            )
        )

    async def send_handoff_notification(
        self,
        task_id: str,
        handoff_id: str,
        from_agent: str | None,
        to_documenter: str,
    ) -> None:
        """Send notification that task needs handoff documentation."""
        logger.info(
            "Sending handoff notification",
            task_id=task_id,
            handoff_id=handoff_id,
            to_documenter=to_documenter,
        )

        body = (
            f"Task {task_id} is ready for handoff (ID: {handoff_id}).\n\n"
            "Please review and create handoff documentation."
        )
        await self._create_notification(
            CreateNotificationParams(
                notification_type=NotificationType.DOCUMENTATION_REQUEST,
                priority=NotificationPriority.NORMAL,
                from_agent=from_agent or "system",
                to_agents=[to_documenter],
                subject=f"Handoff required: Task {task_id}",
                body=body,
                related_task_id=task_id,
            )
        )

    async def send_qa_failed_notification(
        self,
        task_id: str,
        qa_notes: str,
        to_developer: str,
    ) -> None:
        """Send notification that task failed QA."""
        logger.info(
            "Sending QA failed notification",
            task_id=task_id,
            to_developer=to_developer,
        )

        body = (
            f"Task {task_id} has failed QA review.\n\n"
            f"Notes: {qa_notes}\n\n"
            "Please address the issues and resubmit."
        )
        await self._create_notification(
            CreateNotificationParams(
                notification_type=NotificationType.REVIEW_REQUEST,
                priority=NotificationPriority.HIGH,
                from_agent="system",
                to_agents=[to_developer],
                subject=f"QA Failed: Task {task_id}",
                body=body,
                related_task_id=task_id,
            )
        )

    async def send_a2a_notification(
        self,
        task_id: str,
        a2a_context: dict[str, Any],
    ) -> None:
        """Send notification for A2A request (when recipient is busy or offline).

        Args:
            task_id: Related task ID
            a2a_context: Dict with from_agent, to_agent, skill, message, urgent
        """
        from_agent = a2a_context.get("from_agent", "unknown")
        to_agent = a2a_context.get("to_agent", "")
        skill = a2a_context.get("skill", "general")
        message = a2a_context.get("message", "")
        urgent = a2a_context.get("urgent", False)

        logger.info(
            "Sending A2A notification",
            task_id=task_id,
            from_agent=from_agent,
            to_agent=to_agent,
            skill=skill,
            urgent=urgent,
        )

        urgency_label = "[URGENT] " if urgent else ""
        body = (
            f"{urgency_label}A2A request from {from_agent}.\n\n"
            f"Skill: {skill}\n\n"
            f"Message: {message}"
        )
        priority = (
            NotificationPriority.URGENT if urgent else NotificationPriority.NORMAL
        )
        await self._create_notification(
            CreateNotificationParams(
                notification_type=NotificationType.A2A_REQUEST,
                priority=priority,
                from_agent=from_agent,
                to_agents=[to_agent],
                subject=f"{urgency_label}A2A: {skill}",
                body=body,
                related_task_id=task_id,
            )
        )

    async def _create_notification(self, params: CreateNotificationParams) -> None:
        """Create a notification via the database and deliver it."""
        async with get_db_context() as db:
            from_agent_uuid = await _resolve_agent_uuid(db, params.from_agent)
            if from_agent_uuid is None:
                # Caller passed "system" / "unknown" / an unknown slug.
                # notifications.from_agent is NOT NULL + FK to agents.id,
                # so we cannot insert. Skip-with-warn rather than crash
                # the upstream request — the notification would be
                # orphaned anyway (no sender the recipient could reply to).
                logger.warning(
                    "Skipping notification: from_agent could not be resolved to an agent UUID",  # noqa: E501
                    from_agent_input=str(params.from_agent),
                    type=params.notification_type.value
                    if hasattr(params.notification_type, "value")
                    else str(params.notification_type),
                    subject=params.subject[:80],
                    to_agents=[str(a) for a in params.to_agents],
                )
                return
            notification = NotificationTable(
                type=params.notification_type,
                priority=params.priority,
                from_agent=from_agent_uuid,
                to_agents=params.to_agents,
                subject=params.subject,
                body=params.body,
                related_task_id=params.related_task_id,
            )
            db.add(notification)
            await db.flush()

            # Deliver via Redis Streams for real-time push
            from roboco.services.notification_delivery import (
                get_notification_delivery_service,
            )

            delivery_service = get_notification_delivery_service(db)
            await delivery_service.deliver(require_uuid(notification.id))

            await db.commit()

            logger.info(
                "Notification created and delivered",
                notification_id=str(notification.id),
                type=params.notification_type.value,
            )
