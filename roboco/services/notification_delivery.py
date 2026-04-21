"""
Notification Delivery Service

Handles delivery of notifications to agents through multiple channels:
1. WebSocket (real-time push for connected agents)
2. Redis pub/sub (for polling/background delivery)
3. Database queue (persistent fallback)

Also implements the ACK system for tracking acknowledgments.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar, Literal
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.agents_config import (
    get_escalation_target,
    get_pm_for_agent,
    get_pm_for_team,
)
from roboco.db.tables import AgentTable, NotificationTable, TaskTable
from roboco.events import Event, EventType, get_event_bus
from roboco.models.base import AgentRole, NotificationPriority, NotificationType
from roboco.services.base import BaseService, NotFoundError
from roboco.utils.converters import require_uuid


class EscalationError(ValueError):
    """Raised when an escalation can't be routed (missing chain, bad override)."""


@dataclass(frozen=True)
class EscalationOutcome:
    """Result of `NotificationDeliveryService.escalate_and_notify`."""

    target_slug: str
    target_agent_id: UUID
    escalator_slug: str


@dataclass(frozen=True)
class BlockerDetails:
    """Blocker information supplied by the agent calling soft-block."""

    blocker_type: str
    reason: str
    what_needed: str


@dataclass(frozen=True)
class PMRejectDetails:
    """Metadata for a PM-reject developer notification."""

    from_role: str
    developer_agent_id: UUID
    notes: str | None


@dataclass(frozen=True)
class ApiNotificationCreate:
    """Service-side view of the API's notification-send request.

    Avoids importing api/schemas types into the service layer; the route
    pydantic model is translated into this dataclass at the boundary.
    """

    type: Any
    priority: Any
    to_agents: list[UUID]
    subject: str
    body: str
    requires_ack: bool
    related_task_id: UUID | None
    expires_at: Any | None


class NotificationDeliveryService(BaseService):
    """
    Service for delivering notifications to agents.

    Provides:
    - Delivery through multiple channels (WebSocket, Redis, DB)
    - Delivery status tracking
    - ACK system (received + read)
    - Pending notification queries

    Usage:
        service = NotificationDeliveryService(db_session)

        # Get pending notifications for an agent
        pending = await service.get_pending_for_agent(agent_id)

        # Acknowledge a notification
        await service.acknowledge(notification_id, agent_id, "received")
    """

    service_name: ClassVar[str] = "notification_delivery"

    # =========================================================================
    # DELIVERY OPERATIONS (TASK-016)
    # =========================================================================

    async def deliver(self, notification_id: UUID) -> bool:
        """
        Deliver a notification to its recipients.

        Attempts delivery through:
        1. WebSocket (if agent connected) - immediate push
        2. Redis pub/sub - for polling agents
        3. Database - persistent storage (always)

        Returns True if at least one delivery channel succeeded.
        """
        notification = await self.get_notification(notification_id)
        if not notification:
            self.log.warning(
                "Notification not found", notification_id=str(notification_id)
            )
            return False

        # Mark delivery attempted
        notification.delivered_at = datetime.now(UTC)
        await self.session.flush()

        # Publish to Redis for real-time delivery
        try:
            bus = get_event_bus()
            if bus.is_connected():
                # SQLAlchemy normally hydrates Enum columns back to enum
                # members, but a handful of code paths feed raw strings in
                # (e.g. direct dict construction in bulk-insert helpers) and
                # those round-trip as plain str on read. Coerce defensively:
                # an enum has `.value`, a str is its own value.
                def _enum_value(v: object) -> object:
                    return v.value if hasattr(v, "value") else v

                for recipient_id in notification.to_agents:
                    await bus.publish(
                        Event(
                            type=EventType.NOTIFICATION_SENT,
                            data={
                                "notification_id": str(notification_id),
                                "recipient_id": str(recipient_id),
                                "type": _enum_value(notification.type),
                                "priority": _enum_value(notification.priority),
                                "subject": notification.subject,
                            },
                        )
                    )
                self.log.info(
                    "Notification published to Redis",
                    notification_id=str(notification_id),
                    recipient_count=len(notification.to_agents),
                )
        except Exception as e:
            self.log.warning(
                "Failed to publish notification to Redis",
                notification_id=str(notification_id),
                error=str(e),
            )

        return True

    async def get_notification(self, notification_id: UUID) -> NotificationTable | None:
        """Get a notification by ID."""
        result = await self.session.execute(
            select(NotificationTable).where(NotificationTable.id == notification_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    def _notification_is_fully_acked(n: NotificationTable) -> bool:
        """Every recipient has already acknowledged the notification?"""
        acked = {str(a) for a in (n.acked_by or [])}
        return not any(str(r) not in acked for r in (n.to_agents or []))

    def _log_expired_notification(self, n: NotificationTable) -> None:
        """Emit a single 'expired-without-full-ACK' warning log line."""
        self.log.warning(
            "Notification expired without full ACK",
            notification_id=str(n.id),
            type=n.type.value if n.type else None,
            priority=n.priority.value if n.priority else None,
            recipient_count=len(n.to_agents or []),
            ack_count=len(n.acked_by or []),
            expired_at=n.expires_at.isoformat() if n.expires_at else None,
        )

    async def sweep_expired_notifications(self) -> int:
        """Log notifications past their `expires_at` that still require ACK.

        `NotificationTable.expires_at` existed but nothing acted on it. This
        sweep surfaces notifications that have become stale so an operator
        (or an escalation follow-up) can decide what to do. We log rather
        than auto-cancel because the notification is the record; rewriting
        status would be ambiguous. Returns the count of stale items.
        """
        now = datetime.now(UTC)
        result = await self.session.execute(
            select(NotificationTable).where(
                and_(
                    NotificationTable.expires_at.is_not(None),
                    NotificationTable.expires_at < now,
                    NotificationTable.requires_ack.is_(True),
                )
            )
        )
        stale = list(result.scalars().all())

        unacked = [n for n in stale if not self._notification_is_fully_acked(n)]
        for n in unacked:
            self._log_expired_notification(n)
        return len(unacked)

    async def get_pending_for_agent(
        self,
        agent_id: UUID,
        limit: int = 20,
        include_read: bool = False,
    ) -> list[NotificationTable]:
        """
        Get pending notifications for an agent.

        Args:
            agent_id: Agent to get notifications for
            limit: Maximum notifications to return
            include_read: Include already-read notifications

        Returns:
            List of notifications (newest first)
        """
        # Query notifications where agent is in to_agents
        query = select(NotificationTable).where(
            NotificationTable.to_agents.contains([agent_id])
        )

        if not include_read:
            # Exclude notifications already read by this agent
            query = query.where(~NotificationTable.read_by.contains([agent_id]))

        query = query.order_by(NotificationTable.timestamp.desc()).limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_unacknowledged_for_agent(
        self,
        agent_id: UUID,
        limit: int = 20,
    ) -> list[NotificationTable]:
        """
        Get notifications requiring ACK that haven't been acknowledged.

        Args:
            agent_id: Agent to get notifications for
            limit: Maximum notifications to return

        Returns:
            List of unacknowledged notifications
        """
        query = (
            select(NotificationTable)
            .where(
                and_(
                    NotificationTable.to_agents.contains([agent_id]),
                    NotificationTable.requires_ack.is_(True),
                    ~NotificationTable.acked_by.contains([agent_id]),
                )
            )
            .order_by(NotificationTable.timestamp.desc())
            .limit(limit)
        )

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_notification_count(
        self,
        agent_id: UUID,
    ) -> dict[str, int]:
        """
        Get notification counts for an agent.

        Returns:
            Dict with counts: total, unread, pending_ack
        """
        # Get all notifications for agent
        base_query = select(NotificationTable).where(
            NotificationTable.to_agents.contains([agent_id])
        )

        result = await self.session.execute(base_query)
        notifications = list(result.scalars().all())

        total = len(notifications)
        unread = sum(1 for n in notifications if agent_id not in n.read_by)
        pending_ack = sum(
            1 for n in notifications if n.requires_ack and agent_id not in n.acked_by
        )

        return {
            "total": total,
            "unread": unread,
            "pending_ack": pending_ack,
        }

    # =========================================================================
    # ACK OPERATIONS (TASK-017)
    # =========================================================================

    async def acknowledge(
        self,
        notification_id: UUID,
        agent_id: UUID,
        ack_type: Literal["received", "read"] = "received",
    ) -> NotificationTable | None:
        """
        Acknowledge a notification.

        Args:
            notification_id: Notification to acknowledge
            agent_id: Agent acknowledging
            ack_type: Type of acknowledgment:
                - "received": Agent's system received it
                - "read": Agent has read/processed it

        Returns:
            Updated notification or None if not found

        Raises:
            ValueError: If agent is not a recipient
        """
        notification = await self.get_notification(notification_id)
        if not notification:
            return None

        # Verify agent is a recipient
        if agent_id not in notification.to_agents:
            raise ValueError("Agent is not a recipient of this notification")

        now = datetime.now(UTC)

        # Add to acked_by if received ACK and not already there
        if ack_type == "received" and agent_id not in notification.acked_by:
            new_acked = [*notification.acked_by, agent_id]
            notification.acked_by = new_acked
            notification.acked_at = {
                **notification.acked_at,
                str(agent_id): now.isoformat(),
            }

        # Both types mark as read
        if agent_id not in notification.read_by:
            notification.read_by = [*notification.read_by, agent_id]

        await self.session.flush()

        # Publish ACK event
        try:
            bus = get_event_bus()
            if bus.is_connected():
                await bus.publish(
                    Event(
                        type=EventType.NOTIFICATION_ACKED,
                        data={
                            "notification_id": str(notification_id),
                            "agent_id": str(agent_id),
                            "ack_type": ack_type,
                        },
                    )
                )
        except Exception as e:
            self.log.warning("Failed to publish ACK event", error=str(e))

        self.log.info(
            "Notification acknowledged",
            notification_id=str(notification_id),
            agent_id=str(agent_id),
            ack_type=ack_type,
        )
        return notification

    async def mark_read(
        self,
        notification_id: UUID,
        agent_id: UUID,
    ) -> NotificationTable | None:
        """
        Mark a notification as read (without full ACK).

        This is for tracking that the agent has seen the notification,
        but doesn't count as formal acknowledgment.
        """
        return await self.acknowledge(notification_id, agent_id, "read")

    async def bulk_acknowledge(
        self,
        notification_ids: list[UUID],
        agent_id: UUID,
        ack_type: Literal["received", "read"] = "received",
    ) -> int:
        """
        Acknowledge multiple notifications at once.

        Returns number of notifications acknowledged.
        """
        count = 0
        for notification_id in notification_ids:
            try:
                result = await self.acknowledge(notification_id, agent_id, ack_type)
                if result:
                    count += 1
            except ValueError:
                # Agent not a recipient - skip
                continue
        return count

    # =========================================================================
    # SUMMARY & STATUS
    # =========================================================================

    async def get_ack_status(
        self,
        notification_id: UUID,
    ) -> dict | None:
        """
        Get acknowledgment status for a notification.

        Returns dict with:
        - total_recipients: Number of recipients
        - acknowledged: Number who have ACKed
        - read: Number who have read
        - pending: List of agent IDs who haven't ACKed
        """
        notification = await self.get_notification(notification_id)
        if not notification:
            return None

        total = len(notification.to_agents)
        acknowledged = len(notification.acked_by)
        read_count = len(notification.read_by)
        pending = [
            str(aid)
            for aid in notification.to_agents
            if aid not in notification.acked_by
        ]

        return {
            "notification_id": str(notification_id),
            "total_recipients": total,
            "acknowledged": acknowledged,
            "read": read_count,
            "pending": pending,
            "is_fully_acknowledged": acknowledged == total,
        }

    async def get_delivery_summary(
        self,
        agent_id: UUID,
    ) -> dict:
        """
        Get delivery summary for an agent.

        Returns counts and lists useful for UI display.
        """
        # Get counts
        counts = await self.get_notification_count(agent_id)

        # Get urgent unread
        urgent_query = (
            select(NotificationTable)
            .where(
                and_(
                    NotificationTable.to_agents.contains([agent_id]),
                    ~NotificationTable.read_by.contains([agent_id]),
                    NotificationTable.priority == NotificationPriority.URGENT,
                )
            )
            .limit(5)
        )
        urgent_result = await self.session.execute(urgent_query)
        urgent = [
            {
                "id": str(n.id),
                "subject": n.subject,
                "from": str(n.from_agent),
                "timestamp": n.timestamp.isoformat(),
            }
            for n in urgent_result.scalars().all()
        ]

        return {
            "counts": counts,
            "urgent_notifications": urgent,
        }

    # =========================================================================
    # TASK HANDOFF NOTIFICATIONS
    # =========================================================================
    # These compose the full "resolve recipient + persist + deliver" pattern
    # that used to live as private helpers inside api/routes/tasks.py. Routes
    # should only call these — no NotificationTable construction in route modules.

    async def notify_pm_of_block(
        self,
        *,
        task: TaskTable,
        task_id: UUID,
        blocker_agent_id: UUID,
        details: BlockerDetails,
    ) -> None:
        """Create + deliver a blocker_escalation notification to the task PM."""
        pm = await self._resolve_team_pm(task)
        if not pm:
            return

        blocker = await self._get_agent_by_id(blocker_agent_id)
        blocker_name = blocker.slug if blocker else "Unknown agent"
        task_title = task.title or "Untitled"

        notification = NotificationTable(
            type="blocker_escalation",
            priority="high",
            from_agent=blocker_agent_id,
            to_agents=[pm.id],
            subject=f"🚫 ACTION REQUIRED: Blocked - {task_title[:40]}",
            body=(
                f"Task {task_id} has been BLOCKED by {blocker_name}.\n\n"
                f"Type: {details.blocker_type}\n"
                f"Reason: {details.reason}\n"
                f"What's needed: {details.what_needed}\n\n"
                "⚠️ ACTION REQUIRED:\n"
                "When resolved, you MUST call:\n"
                f"  roboco_task_unblock('{task_id}')\n\n"
                "Verbal resolution in chat is NOT enough - "
                "the task will remain blocked until you call the tool."
            ),
            related_task_id=task_id,
            requires_ack=True,
            read_by=[],
            acked_by=[],
        )
        await self._persist_and_deliver(notification)

    async def notify_pm_of_docs_complete(
        self,
        *,
        task: TaskTable,
        task_id: UUID,
        submitter_agent_id: UUID,
    ) -> None:
        """Assign task to the docs-handoff PM and deliver a notification."""
        pm = await self._resolve_pm_for_agent_or_team(submitter_agent_id, task)
        if not pm:
            return

        task.assigned_to = pm.id
        notification = NotificationTable(
            type="task_assignment",
            priority="normal",
            from_agent=submitter_agent_id,
            to_agents=[pm.id],
            subject=f"Documentation complete: {task.title or 'Unknown task'}",
            body=(
                f"Task {task_id} documentation is complete and ready "
                "for final review.\n\nPlease review and complete the task."
            ),
            related_task_id=task_id,
            requires_ack=False,
        )
        await self._persist_and_deliver(notification)

    async def notify_pm_of_review_submission(
        self,
        *,
        task: TaskTable,
        task_id: UUID,
        submitter_agent_id: UUID,
        notes: str | None,
    ) -> None:
        """Assign task to PM + notify that it's ready for review."""
        pm = await self._resolve_pm_for_agent_or_team(submitter_agent_id, task)
        if not pm:
            return

        task.assigned_to = pm.id
        notification = NotificationTable(
            type="task_assignment",
            priority="normal",
            from_agent=submitter_agent_id,
            to_agents=[pm.id],
            subject=f"Task ready for review: {task.title or 'Unknown task'}",
            body=(
                f"Task {task_id} has been submitted for PM review.\n\n"
                f"Notes: {notes or 'None'}\n\n"
                "Please review and complete the task."
            ),
            related_task_id=task_id,
            requires_ack=False,
        )
        await self._persist_and_deliver(notification)

    async def notify_assignee_of_unblock(
        self,
        *,
        task: TaskTable,
        task_id: UUID,
        from_agent_id: UUID,
        assignee_agent_id: UUID,
    ) -> None:
        """Notify the task's assigned agent that their task is unblocked."""
        notification = NotificationTable(
            type="task_assignment",
            priority="high",
            from_agent=from_agent_id,
            to_agents=[assignee_agent_id],
            subject=f"Task unblocked: {task.title or 'Unknown task'}",
            body=(
                f"Task {task_id} has been unblocked and is ready to resume.\n\n"
                "Use roboco_task_get to review the task and continue work."
            ),
            related_task_id=task_id,
            requires_ack=False,
        )
        await self._persist_and_deliver(notification)

    async def notify_developer_of_pm_reject(
        self,
        *,
        task: TaskTable,
        task_id: UUID,
        from_agent_id: UUID,
        details: PMRejectDetails,
    ) -> None:
        """Notify the original developer that PM sent the task back for rework."""
        notification = NotificationTable(
            type="task_assignment",
            priority="high",
            from_agent=from_agent_id,
            to_agents=[details.developer_agent_id],
            subject=f"Rework needed: {task.title or 'Unknown task'}",
            body=(
                f"Task {task_id} sent back for rework by {details.from_role}.\n\n"
                f"Notes: {details.notes or 'see pm_reject_notes in task quick_context'}"
            ),
            related_task_id=task_id,
            requires_ack=True,
        )
        await self._persist_and_deliver(notification)

    async def notify_assignee_of_ceo_rejection(
        self,
        *,
        task: TaskTable,
        task_id: UUID,
        from_agent_id: UUID,
        assignee_agent_id: UUID,
        notes: str,
    ) -> None:
        """Notify the task's assignee that CEO rejected and sent back for revision."""
        notification = NotificationTable(
            type="task_assignment",
            priority="high",
            from_agent=from_agent_id,
            to_agents=[assignee_agent_id],
            subject=f"CEO Revision Required: {task.title or 'Unknown task'}",
            body=(
                f"Task {task_id} was rejected by CEO and requires revision.\n\n"
                f"Reason: {notes}\n\n"
                "Please address the feedback and resubmit."
            ),
            related_task_id=task_id,
            requires_ack=True,
        )
        await self._persist_and_deliver(notification)

    async def escalate_and_notify(
        self,
        *,
        task: TaskTable,
        task_id: UUID,
        escalator_agent_id: UUID,
        reason: str,
        explicit_target_slug: str | None = None,
    ) -> EscalationOutcome:
        """
        Resolve the escalation chain target, persist + deliver a
        blocker_escalation notification, and return the routing outcome.

        Route handlers convert ``EscalationError`` into the right HTTPException.
        """
        escalator = await self._get_agent_by_id(escalator_agent_id)
        if not escalator:
            raise EscalationError(f"escalator agent {escalator_agent_id} not found")

        default_target = get_escalation_target(escalator.slug)
        if not default_target:
            raise EscalationError(
                f"No escalation target configured for {escalator.slug}"
            )
        if explicit_target_slug and explicit_target_slug != default_target:
            raise EscalationError(
                f"Cannot escalate to {explicit_target_slug}. "
                f"Your escalation target is {default_target}."
            )

        target = await self._get_agent_by_slug(default_target)
        if not target:
            raise EscalationError(f"Escalation target not found: {default_target}")

        body = f"Task {task_id} escalated by {escalator.slug}.\n\nReason: {reason}"
        notification = NotificationTable(
            type="blocker_escalation",
            priority="high",
            from_agent=escalator_agent_id,
            to_agents=[target.id],
            subject=f"Escalation: {task.title or 'Unknown task'}",
            body=body,
            related_task_id=task_id,
            requires_ack=True,
            read_by=[],
            acked_by=[],
        )
        await self._persist_and_deliver(notification)
        return EscalationOutcome(
            target_slug=default_target,
            target_agent_id=require_uuid(target.id),
            escalator_slug=escalator.slug,
        )

    async def notify_ceo_of_escalation(
        self,
        *,
        task: TaskTable,
        task_id: UUID,
        escalator_agent_id: UUID,
        escalator_role: str,
        notes: str | None,
    ) -> None:
        """Create + deliver the CEO escalation notification."""
        ceo = await self._get_ceo_agent()
        if not ceo:
            return

        notification = NotificationTable(
            type="task_assignment",
            priority="high",
            from_agent=escalator_agent_id,
            to_agents=[ceo.id],
            subject=f"CEO Approval Required: {task.title or 'Unknown task'}",
            body=(
                f"Task {task_id} requires CEO approval for completion.\n\n"
                f"Escalated by: {escalator_role}\n"
                f"Notes: {notes or 'None'}\n\n"
                "Use /ceo-approve or /ceo-reject to respond."
            ),
            related_task_id=task_id,
            requires_ack=True,
        )
        await self._persist_and_deliver(notification)

    # ------------------------------------------------------------------
    # Private helpers for recipient resolution + persist
    # ------------------------------------------------------------------

    async def _resolve_team_pm(self, task: TaskTable) -> AgentTable | None:
        """Return the PM agent for the task's team, or None if not found."""
        team = task.team
        if not team:
            return None
        pm_slug = get_pm_for_team(team.value)
        if not pm_slug:
            return None
        return await self._get_agent_by_slug(pm_slug)

    async def _resolve_pm_for_agent_or_team(
        self, agent_id: UUID, task: TaskTable
    ) -> AgentTable | None:
        """Prefer the agent's cell-PM; fall back to the task's team-PM."""
        agent = await self._get_agent_by_id(agent_id)
        pm_slug: str | None = None
        if agent and agent.slug:
            pm_slug = get_pm_for_agent(agent.slug)
        if not pm_slug and task.team:
            pm_slug = get_pm_for_team(task.team.value)
        if not pm_slug:
            return None
        return await self._get_agent_by_slug(pm_slug)

    async def _get_agent_by_id(self, agent_id: UUID) -> AgentTable | None:
        result = await self.session.execute(
            select(AgentTable).where(AgentTable.id == agent_id)
        )
        return result.scalar_one_or_none()

    async def _get_agent_by_slug(self, slug: str) -> AgentTable | None:
        result = await self.session.execute(
            select(AgentTable).where(AgentTable.slug == slug)
        )
        return result.scalar_one_or_none()

    async def _get_ceo_agent(self) -> AgentTable | None:
        result = await self.session.execute(
            select(AgentTable).where(AgentTable.role == AgentRole.CEO)
        )
        return result.scalar_one_or_none()

    async def _persist_and_deliver(self, notification: NotificationTable) -> None:
        """Add to session, flush (to get an id), deliver. Caller commits."""
        self.session.add(notification)
        await self.session.flush()
        await self.deliver(require_uuid(notification.id))

    # =========================================================================
    # API-FACING LIST + CRUD (consumed by api/routes/notifications.py)
    # =========================================================================

    async def list_system_notifications(
        self,
        *,
        pending_ack_only: bool,
        type_filter: str | None,
        limit: int,
    ) -> list[NotificationTable]:
        """List every notification (system role only).

        `pending_ack_only` filters post-fetch because "not fully acked" isn't
        a SQL-friendly predicate against PostgreSQL array columns.
        """
        query = select(NotificationTable)
        if pending_ack_only:
            query = query.where(NotificationTable.requires_ack.is_(True))
        if type_filter:
            query = query.where(NotificationTable.type == type_filter)
        query = query.order_by(NotificationTable.timestamp.desc()).limit(limit)

        result = await self.session.execute(query)
        notifications = list(result.scalars().all())
        if pending_ack_only:
            return [
                n
                for n in notifications
                if not all(t in n.acked_by for t in n.to_agents)
            ]
        return notifications

    async def list_for_agent(
        self,
        *,
        agent_id: UUID,
        unread_only: bool,
        pending_ack_only: bool,
        type_filter: NotificationType | None,
        limit: int,
    ) -> list[NotificationTable]:
        """Return notifications addressed to `agent_id` with the given filters.

        Query construction and execution both live here so route modules
        never touch `NotificationTable` or `db.execute`.
        """
        query = select(NotificationTable).where(
            NotificationTable.to_agents.contains([agent_id])
        )
        if unread_only:
            query = query.where(~NotificationTable.read_by.contains([agent_id]))
        if pending_ack_only:
            query = query.where(
                NotificationTable.requires_ack.is_(True),
                ~NotificationTable.acked_by.contains([agent_id]),
            )
        if type_filter is not None:
            query = query.where(NotificationTable.type == type_filter)
        query = query.order_by(NotificationTable.timestamp.desc()).limit(limit)

        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_for_recipient_and_mark_read(
        self, *, notification_id: UUID, agent_id: UUID
    ) -> NotificationTable:
        """Fetch a notification for a recipient and auto-mark read."""
        notification = await self.get_notification(notification_id)
        if notification is None:
            raise NotFoundError(
                resource_type="Notification", resource_id=str(notification_id)
            )
        if agent_id not in notification.to_agents:
            raise PermissionError("view notification: not a recipient")

        if agent_id not in notification.read_by:
            notification.read_by = [*notification.read_by, agent_id]
            await self.session.flush()
        return notification

    async def acknowledge_for_recipient(
        self, *, notification_id: UUID, agent_id: UUID
    ) -> NotificationTable:
        """Ack a notification that requires it; raises if not allowed."""
        notification = await self.get_notification(notification_id)
        if notification is None:
            raise NotFoundError(
                resource_type="Notification", resource_id=str(notification_id)
            )
        if agent_id not in notification.to_agents:
            raise PermissionError("acknowledge notification: not a recipient")
        if not notification.requires_ack:
            raise ValueError("This notification does not require acknowledgment")

        now = datetime.now(UTC)
        if agent_id not in notification.acked_by:
            notification.acked_by = [*notification.acked_by, agent_id]
            notification.acked_at = {
                **notification.acked_at,
                str(agent_id): now.isoformat(),
            }
        if agent_id not in notification.read_by:
            notification.read_by = [*notification.read_by, agent_id]

        await self.session.flush()
        return notification

    async def mark_read_for_recipient(
        self, *, notification_id: UUID, agent_id: UUID
    ) -> None:
        """Mark a notification read on behalf of a recipient."""
        notification = await self.get_notification(notification_id)
        if notification is None:
            raise NotFoundError(
                resource_type="Notification", resource_id=str(notification_id)
            )
        if agent_id not in notification.to_agents:
            raise PermissionError("mark notification read: not a recipient")
        if agent_id not in notification.read_by:
            notification.read_by = [*notification.read_by, agent_id]
            await self.session.flush()

    async def send_from_api(
        self,
        *,
        sender_agent_id: UUID,
        data: "ApiNotificationCreate",
    ) -> NotificationTable:
        """Resolve sender/recipient slugs, validate permissions, persist, deliver."""
        from roboco.enforcement import (
            NotificationPermissionError,
            validate_notification_permission,
        )

        agent = await self._get_agent_by_id(sender_agent_id)
        if not agent:
            raise NotFoundError(resource_type="Agent", resource_id=str(sender_agent_id))

        recipient_slugs: list[str] = []
        for recipient_uuid in data.to_agents:
            recipient = await self._get_agent_by_id(recipient_uuid)
            if recipient:
                recipient_slugs.append(recipient.slug)

        try:
            validate_notification_permission(
                sender_id=agent.slug, recipients=recipient_slugs
            )
        except NotificationPermissionError:
            # Re-raise — route converts to 403.
            raise

        notification = NotificationTable(
            type=data.type,
            priority=data.priority,
            from_agent=sender_agent_id,
            to_agents=data.to_agents,
            subject=data.subject,
            body=data.body,
            requires_ack=data.requires_ack,
            related_task_id=data.related_task_id,
            expires_at=data.expires_at,
        )
        await self._persist_and_deliver(notification)
        return notification

    async def has_pending_a2a(
        self, *, from_agent_id: UUID, to_agent_id: UUID, task_id: UUID
    ) -> bool:
        """True iff there's an unacked A2A_REQUEST from→to about this task."""
        result = await self.session.execute(
            select(NotificationTable).where(
                NotificationTable.type == NotificationType.A2A_REQUEST,
                NotificationTable.from_agent == from_agent_id,
                NotificationTable.related_task_id == task_id,
                NotificationTable.to_agents.contains([to_agent_id]),
            )
        )
        return any(to_agent_id not in n.acked_by for n in result.scalars().all())

    async def auto_ack_a2a(
        self, *, from_agent_id: UUID, to_agent_id: UUID, task_id: UUID
    ) -> int:
        """Ack every A2A_REQUEST from→to about this task. Returns count acked."""
        result = await self.session.execute(
            select(NotificationTable).where(
                NotificationTable.type == NotificationType.A2A_REQUEST,
                NotificationTable.from_agent == from_agent_id,
                NotificationTable.related_task_id == task_id,
                NotificationTable.to_agents.contains([to_agent_id]),
            )
        )
        now = datetime.now(UTC).isoformat()
        acked = 0
        touched = False
        for notif in result.scalars().all():
            if to_agent_id not in notif.acked_by:
                notif.acked_by = [*notif.acked_by, to_agent_id]
                notif.acked_at = {**notif.acked_at, str(to_agent_id): now}
                acked += 1
                touched = True
            if to_agent_id not in notif.read_by:
                notif.read_by = [*notif.read_by, to_agent_id]
                touched = True
        if touched:
            await self.session.flush()
        return acked


# =============================================================================
# SERVICE FACTORY
# =============================================================================


def get_notification_delivery_service(
    session: AsyncSession,
) -> NotificationDeliveryService:
    """Factory function to create a NotificationDeliveryService instance."""
    return NotificationDeliveryService(session)
