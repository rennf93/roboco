"""
Notification Delivery Service

Handles delivery of notifications to agents through multiple channels:
1. WebSocket (real-time push for connected agents)
2. Redis pub/sub (for polling/background delivery)
3. Database queue (persistent fallback)

Also implements the ACK system for tracking acknowledgments.
"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar, Literal, cast
from uuid import UUID

import structlog
from sqlalchemy import and_, event, select
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.agents_config import (
    get_escalation_target,
    get_pm_for_agent,
    get_pm_for_team,
)
from roboco.db.tables import AgentTable, NotificationTable, TaskTable
from roboco.events import Event, EventType, get_event_bus
from roboco.foundation.policy.communications import ACK_REQUIRED_BY_TYPE
from roboco.models.base import AgentRole, NotificationPriority, NotificationType
from roboco.services.base import BaseService, NotFoundError
from roboco.services.notification_dedup import all_recipients_recently_notified
from roboco.utils.converters import require_uuid

_log = structlog.get_logger(service="notification_delivery")


# =============================================================================
# Deferred bus publish — transactional outbox (F107)
# =============================================================================
# `deliver`/`_persist_and_deliver` run inside the caller's open transaction:
# the notification row is flushed but not committed. Publishing the
# NOTIFICATION_SENT event to the Redis bus *before* the commit created
# phantom notifications — a commit failure (DB hiccup, constraint, asyncpg
# error) rolled the row back while connected WebSocket clients had already
# received a push for an id that no longer existed. The fix defers the bus
# publish to the session's `after_commit` so a rollback drops the pending
# event: the row is durable by the time the event fires.
#
# The pending events and the scheduled drain tasks live on `session.info` so
# they are scoped to the session's lifetime (no module-global state, no
# cross-request leak). A sync `after_commit` listener schedules the async
# drain via `asyncio.create_task` (the listener runs synchronously inside
# `await AsyncSession.commit()` on the loop thread, so the running loop is
# available); `after_rollback` clears the pending queue so a rolled-back
# transaction emits nothing.

_PENDING_PUBLISHES_KEY = "_roboco_pending_bus_publishes"
_DRAIN_TASKS_KEY = "_roboco_drain_tasks"
_DRAIN_REGISTERED_KEY = "_roboco_drain_registered"


async def _drain_pending_publishes(pending: list[Event]) -> None:
    """Publish every deferred event best-effort once the txn has committed.

    The bus is read fresh at drain time (it may have reconnected between
    deferral and commit); a disconnected bus is a silent no-op, matching the
    prior inline behavior. Each publish is independent — one failure does
    not drop the rest.
    """
    if not pending:
        return
    bus = get_event_bus()
    if not bus.is_connected():
        return
    for ev in pending:
        try:
            await bus.publish(ev)
        except Exception as e:  # best-effort: never break the drain
            _log.warning("Deferred bus publish failed", error=str(e))


def _schedule_pending_publishes(session: AsyncSession) -> None:
    """`after_commit` handler: hand the pending events to the running loop.

    Sync listener — runs inside `await AsyncSession.commit()`, so the event
    loop is active. The created task is stashed on the session so callers /
    tests can await it deterministically; in production it is fire-and-forget
    (best-effort, matching the prior try/except semantics).
    """
    pending = session.info.pop(_PENDING_PUBLISHES_KEY, None)
    if not pending:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # no running loop — nothing we can do, drop silently
        return
    task = loop.create_task(_drain_pending_publishes(pending))
    session.info.setdefault(_DRAIN_TASKS_KEY, []).append(task)


def _discard_pending_publishes(session: AsyncSession) -> None:
    """`after_rollback` handler: a rolled-back txn emits nothing (no phantom)."""
    session.info.pop(_PENDING_PUBLISHES_KEY, None)


def defer_bus_publish(session: AsyncSession, ev: Event) -> None:
    """Enqueue a bus event to fire only after the session's transaction commits.

    Registers one-shot `after_commit` / `after_rollback` listeners on the
    session the first time it is called for that session; subsequent calls
    just append. The listeners are bound to the session instance and are
    collected with it (no global listener accumulation).
    """
    session.info.setdefault(_PENDING_PUBLISHES_KEY, []).append(ev)
    if session.info.get(_DRAIN_REGISTERED_KEY):
        return
    session.info[_DRAIN_REGISTERED_KEY] = True

    sync_session = session.sync_session

    @event.listens_for(sync_session, "after_commit")
    def _on_commit(_sync_session: object) -> None:
        _schedule_pending_publishes(session)

    @event.listens_for(sync_session, "after_rollback")
    def _on_rollback(_sync_session: object) -> None:
        _discard_pending_publishes(session)


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

        The Redis/bus publish is deferred until the caller's transaction
        commits (`defer_bus_publish`) — publishing before the commit produced
        phantom notifications when the commit failed (F107). The `delivered_at`
        DB marker is written inside the transaction (it rolls back with the
        row if the commit fails), and the bus event fires only once the row is
        durable.

        Returns True if at least one delivery channel succeeded.
        """
        notification = await self.get_notification(notification_id)
        if not notification:
            self.log.warning(
                "Notification not found", notification_id=str(notification_id)
            )
            return False

        # Mark delivery attempted (in-tx — rolls back with the row on
        # commit failure, so the marker and the row stay consistent).
        notification.delivered_at = datetime.now(UTC)
        await self.session.flush()

        # Build the per-recipient bus events up front (the data is materialized
        # to strings, so deferring is safe even if the ORM object later
        # expires) and defer each to the session's after_commit. The event is
        # dropped on rollback (no phantom) and fired once the row is durable.
        # Best-effort: a bus-init failure is logged but never propagates — the
        # notification row + delivered_at marker are already flushed, and the
        # bus is a secondary delivery channel (the row is the durable store).
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
                    defer_bus_publish(
                        self.session,
                        Event(
                            type=EventType.NOTIFICATION_SENT,
                            data={
                                "notification_id": str(notification_id),
                                "recipient_id": str(recipient_id),
                                "type": _enum_value(notification.type),
                                "priority": _enum_value(notification.priority),
                                "subject": notification.subject,
                            },
                        ),
                    )
                self.log.info(
                    "Notification bus publish deferred until commit",
                    notification_id=str(notification_id),
                    recipient_count=len(notification.to_agents),
                )
        except Exception as e:
            self.log.warning(
                "Failed to defer notification bus publish",
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
            type=NotificationType.BLOCKER_ESCALATION,
            priority=NotificationPriority.HIGH,
            from_agent=blocker_agent_id,
            to_agents=[pm.id],
            subject=f"ACTION REQUIRED: Blocked - {task_title[:40]}",
            body=(
                f"Task {task_id} has been BLOCKED by {blocker_name}.\n\n"
                f"Type: {details.blocker_type}\n"
                f"Reason: {details.reason}\n"
                f"What's needed: {details.what_needed}\n\n"
                "ACTION REQUIRED:\n"
                "When resolved, you MUST call:\n"
                f"  unblock('{task_id}')\n\n"
                "Verbal resolution in chat is NOT enough - "
                "the task will remain blocked until you call the tool."
            ),
            related_task_id=task_id,
            requires_ack=ACK_REQUIRED_BY_TYPE[NotificationType.BLOCKER_ESCALATION],
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
            type=NotificationType.TASK_ASSIGNMENT,
            priority=NotificationPriority.NORMAL,
            from_agent=submitter_agent_id,
            to_agents=[pm.id],
            subject=f"Documentation complete: {task.title or 'Unknown task'}",
            body=(
                f"Task {task_id} documentation is complete and ready "
                "for final review.\n\nPlease review and complete the task."
            ),
            related_task_id=task_id,
            requires_ack=ACK_REQUIRED_BY_TYPE[NotificationType.TASK_ASSIGNMENT],
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
            type=NotificationType.TASK_ASSIGNMENT,
            priority=NotificationPriority.NORMAL,
            from_agent=submitter_agent_id,
            to_agents=[pm.id],
            subject=f"Task ready for review: {task.title or 'Unknown task'}",
            body=(
                f"Task {task_id} has been submitted for PM review.\n\n"
                f"Notes: {notes or 'None'}\n\n"
                "Please review and complete the task."
            ),
            related_task_id=task_id,
            requires_ack=ACK_REQUIRED_BY_TYPE[NotificationType.TASK_ASSIGNMENT],
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
            type=NotificationType.TASK_ASSIGNMENT,
            priority=NotificationPriority.HIGH,
            from_agent=from_agent_id,
            to_agents=[assignee_agent_id],
            subject=f"Task unblocked: {task.title or 'Unknown task'}",
            body=(
                f"Task {task_id} has been unblocked and is ready to resume.\n\n"
                "Review the task details in your briefing and continue work."
            ),
            related_task_id=task_id,
            requires_ack=ACK_REQUIRED_BY_TYPE[NotificationType.TASK_ASSIGNMENT],
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
            type=NotificationType.APPROVAL,
            priority=NotificationPriority.HIGH,
            from_agent=from_agent_id,
            to_agents=[assignee_agent_id],
            subject=f"CEO Revision Required: {task.title or 'Unknown task'}",
            body=(
                f"Task {task_id} was rejected by CEO and requires revision.\n\n"
                f"Reason: {notes}\n\n"
                "Please address the feedback and resubmit."
            ),
            related_task_id=task_id,
            requires_ack=ACK_REQUIRED_BY_TYPE[NotificationType.APPROVAL],
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
            type=NotificationType.BLOCKER_ESCALATION,
            priority=NotificationPriority.HIGH,
            from_agent=escalator_agent_id,
            to_agents=[target.id],
            subject=f"Escalation: {task.title or 'Unknown task'}",
            body=body,
            related_task_id=task_id,
            requires_ack=ACK_REQUIRED_BY_TYPE[NotificationType.BLOCKER_ESCALATION],
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
            type=NotificationType.APPROVAL,
            priority=NotificationPriority.HIGH,
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
            requires_ack=ACK_REQUIRED_BY_TYPE[NotificationType.APPROVAL],
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
        # Re-fire guard (loop-prone types): this path skips the DB dedup, so
        # apply the same 60s Redis SET-NX window. Fail-open on Redis down.
        # Casts peel the SA UUID column type-leak for the type checker.
        if await all_recipients_recently_notified(
            ntype=notification.type,
            from_agent=cast("UUID | None", notification.from_agent),
            recipients=cast("list[UUID]", notification.to_agents),
            related_task_id=cast("UUID | None", notification.related_task_id),
        ):
            _log.info(
                "Suppressed re-fire notification (loop-prone, recent window)",
                from_agent=str(notification.from_agent)
                if notification.from_agent is not None
                else None,
                type=notification.type.value if notification.type is not None else None,
                related_task_id=str(notification.related_task_id)
                if notification.related_task_id is not None
                else None,
            )
            return
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


# =============================================================================
# SERVICE FACTORY
# =============================================================================


def get_notification_delivery_service(
    session: AsyncSession,
) -> NotificationDeliveryService:
    """Factory function to create a NotificationDeliveryService instance."""
    return NotificationDeliveryService(session)
