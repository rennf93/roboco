"""
Notification Service

Sends notifications through the API with proper enforcement.
"""

from uuid import UUID

import structlog

from roboco.db import get_async_session
from roboco.models import NotificationPriority, NotificationType

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
        await self._create_notification(
            notification_type=NotificationType.ESCALATION,
            priority=NotificationPriority.HIGH,
            from_agent=from_agent or "system",
            to_agents=[to_pm],
            subject=f"Task {task_id} is blocked",
            body=f"Task {task_id} has been blocked.\n\nReason: {blocker_reason}\n\nPlease investigate and help resolve.",
            related_task_id=task_id,
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

        await self._create_notification(
            notification_type=NotificationType.TASK_ASSIGNMENT,
            priority=NotificationPriority.NORMAL,
            from_agent=from_agent or "system",
            to_agents=[to_qa],
            subject=f"Task {task_id} ready for QA",
            body=f"Task {task_id} has been submitted for QA review.\n\nPlease review the implementation and acceptance criteria.",
            related_task_id=task_id,
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

        await self._create_notification(
            notification_type=NotificationType.STATUS_CHANGE,
            priority=NotificationPriority.HIGH,
            from_agent="system",
            to_agents=[to_developer],
            subject=f"Task {task_id} needs revision",
            body=f"Task {task_id} did not pass QA review.\n\nQA Notes:\n{qa_notes}\n\nPlease address the feedback and resubmit.",
            related_task_id=task_id,
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

        await self._create_notification(
            notification_type=NotificationType.TASK_ASSIGNMENT,
            priority=NotificationPriority.NORMAL,
            from_agent=from_agent or "system",
            to_agents=[to_documenter],
            subject=f"Task {task_id} ready for documentation",
            body=f"Task {task_id} has passed QA and is ready for documentation.\n\nPlease create the handoff documentation.",
            related_task_id=task_id,
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

        await self._create_notification(
            notification_type=NotificationType.HANDOFF,
            priority=NotificationPriority.NORMAL,
            from_agent=from_agent or "system",
            to_agents=[to_documenter],
            subject=f"Handoff ready for task {task_id}",
            body=f"A handoff document has been created for task {task_id}.\n\nHandoff ID: {handoff_id}\n\nPlease review and complete the documentation.",
            related_task_id=task_id,
        )

    async def _create_notification(
        self,
        notification_type: NotificationType,
        priority: NotificationPriority,
        from_agent: str,
        to_agents: list[str],
        subject: str,
        body: str,
        related_task_id: str | None = None,
    ) -> None:
        """Create a notification in the database."""
        from roboco.db.tables import NotificationTable

        async with get_async_session() as session:
            # Look up agent UUIDs from agent_ids
            # For now, we store the string IDs - in production would look up UUIDs
            notification = NotificationTable(
                type=notification_type,
                priority=priority,
                from_agent=self._get_system_agent_uuid(),
                to_agents=[self._agent_id_to_uuid(a) for a in to_agents],
                subject=subject,
                body=body,
                requires_ack=True,
            )

            session.add(notification)
            await session.commit()

            logger.info(
                "Notification created",
                notification_id=str(notification.id),
                to_agents=to_agents,
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
