"""
Notification Service

Sends notifications through the API with proper enforcement.
"""

from dataclasses import dataclass
from uuid import UUID

import structlog

from roboco.db.base import get_db_context
from roboco.models import NotificationPriority, NotificationType


@dataclass
class CreateNotificationParams:
    """Parameters for creating a notification."""

    notification_type: NotificationType
    priority: NotificationPriority
    from_agent: str
    to_agents: list[str]
    subject: str
    body: str
    related_task_id: str | None = None


logger = structlog.get_logger()


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
            f"Task {task_id} has been submitted for QA review.\n\n"
            "Please review the implementation and acceptance criteria."
        )
        await self._create_notification(
            CreateNotificationParams(
                notification_type=NotificationType.TASK_ASSIGNMENT,
                priority=NotificationPriority.NORMAL,
                from_agent=from_agent or "system",
                to_agents=[to_qa],
                subject=f"Task {task_id} ready for QA",
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
            f"Task {task_id} did not pass QA review.\n\n"
            f"QA Notes:\n{qa_notes}\n\n"
            "Please address the feedback and resubmit."
        )
        await self._create_notification(
            CreateNotificationParams(
                notification_type=NotificationType.ALERT,
                priority=NotificationPriority.HIGH,
                from_agent="system",
                to_agents=[to_developer],
                subject=f"Task {task_id} needs revision",
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
            "Please create the handoff documentation."
        )
        await self._create_notification(
            CreateNotificationParams(
                notification_type=NotificationType.TASK_ASSIGNMENT,
                priority=NotificationPriority.NORMAL,
                from_agent=from_agent or "system",
                to_agents=[to_documenter],
                subject=f"Task {task_id} ready for documentation",
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
        """Send notification about handoff creation."""
        logger.info(
            "Sending handoff notification",
            task_id=task_id,
            handoff_id=handoff_id,
            to_documenter=to_documenter,
        )

        body = (
            f"A handoff document has been created for task {task_id}.\n\n"
            f"Handoff ID: {handoff_id}\n\n"
            "Please review and complete the documentation."
        )
        await self._create_notification(
            CreateNotificationParams(
                notification_type=NotificationType.DOCUMENTATION_REQUEST,
                priority=NotificationPriority.NORMAL,
                from_agent=from_agent or "system",
                to_agents=[to_documenter],
                subject=f"Handoff ready for task {task_id}",
                body=body,
                related_task_id=task_id,
            )
        )

    async def _create_notification(self, params: CreateNotificationParams) -> None:
        """Create a notification in the database."""
        from roboco.db.tables import NotificationTable

        async with get_db_context() as session:
            # Look up agent UUIDs from agent_ids
            # For now, we store the string IDs - in production would look up UUIDs
            # Use from_agent if provided, otherwise system agent
            sender_uuid = (
                self._agent_id_to_uuid(params.from_agent)
                if params.from_agent != "system"
                else self._get_system_agent_uuid()
            )
            # Convert task_id to UUID if provided
            task_uuid = UUID(params.related_task_id) if params.related_task_id else None

            notification = NotificationTable(
                type=params.notification_type,
                priority=params.priority,
                from_agent=sender_uuid,
                to_agents=[self._agent_id_to_uuid(a) for a in params.to_agents],
                subject=params.subject,
                body=params.body,
                requires_ack=True,
                related_task_id=task_uuid,
            )

            session.add(notification)
            await session.commit()

            logger.info(
                "Notification created",
                notification_id=str(notification.id),
                to_agents=params.to_agents,
            )

    def _get_system_agent_uuid(self) -> UUID:
        """Get UUID for system notifications."""
        # Use a fixed UUID for system-generated notifications
        return UUID("00000000-0000-0000-0000-000000000000")

    def _agent_id_to_uuid(self, agent_id: str) -> UUID:
        """Convert agent string ID to UUID.

        In production, this would look up the UUID from the database.
        For now, we generate a deterministic UUID from the agent_id.
        """
        import hashlib

        # Create deterministic UUID from agent_id string
        hash_bytes = hashlib.md5(agent_id.encode()).digest()
        return UUID(bytes=hash_bytes)
