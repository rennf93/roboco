"""
Notification Delivery Service

Handles delivery of notifications to agents through multiple channels:
1. WebSocket (real-time push for connected agents)
2. Redis pub/sub (for polling/background delivery)
3. Database queue (persistent fallback)

Also implements the ACK system for tracking acknowledgments.
"""

from datetime import UTC, datetime
from typing import ClassVar, Literal
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.db.tables import NotificationTable
from roboco.events import Event, EventType, get_event_bus
from roboco.models.base import NotificationPriority
from roboco.services.base import BaseService


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

        # Skip notifications that every recipient has already ACK'd.
        unacked = [
            n
            for n in stale
            if any(
                str(r) not in {str(a) for a in (n.acked_by or [])}
                for r in (n.to_agents or [])
            )
        ]

        for n in unacked:
            self.log.warning(
                "Notification expired without full ACK",
                notification_id=str(n.id),
                type=n.type.value if n.type else None,
                priority=n.priority.value if n.priority else None,
                recipient_count=len(n.to_agents or []),
                ack_count=len(n.acked_by or []),
                expired_at=n.expires_at.isoformat() if n.expires_at else None,
            )

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


# =============================================================================
# SERVICE FACTORY
# =============================================================================


def get_notification_delivery_service(
    session: AsyncSession,
) -> NotificationDeliveryService:
    """Factory function to create a NotificationDeliveryService instance."""
    return NotificationDeliveryService(session)
