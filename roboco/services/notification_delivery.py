"""
Notification Delivery Service

Handles delivery of notifications to agents through multiple channels:
1. WebSocket (real-time push for connected agents)
2. Redis pub/sub (for polling/background delivery)
3. Database queue (persistent fallback)

Also implements the ACK system for tracking acknowledgments.
"""

import asyncio
import contextlib
import html
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar, Literal, cast
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
from roboco.services.notification_dedup import (
    all_recipients_recently_notified,
    clear_dedup_key,
)
from roboco.services.notification_text import task_display
from roboco.services.repositories.query_helpers import get_agent_by_role
from roboco.utils.converters import require_uuid

if TYPE_CHECKING:
    from roboco.models.metrics import TaskMetrics

_log = structlog.get_logger(service="notification_delivery")


def _esc(value: object) -> str:
    """HTML-escape a dynamic value before it lands in a Telegram HTML
    message — mirrors ``telegram_inbound._esc``; every DM this service
    composes runs its dynamic parts (a notification subject, a panel link)
    through this before interpolation."""
    return html.escape(str(value), quote=False)


def _esc_attr(value: object) -> str:
    """Like ``_esc`` but also escapes quotes — mirrors
    ``telegram_inbound._esc_attr``. The panel-link ``href`` is the one place
    this service interpolates into an HTML attribute rather than a text
    node; an unescaped ``"`` there would close the attribute early."""
    return html.escape(str(value), quote=True)


def _format_completion_body(task: TaskTable, metrics: "TaskMetrics | None") -> str:
    """Human-readable completion summary — real effort vs wall-clock, not a lone
    wall-clock figure. Degrades to wall-clock-only (turns 'n/a') when there are
    no spawn sessions / pre-turns-migration data."""
    title = task.title or "Untitled"
    if metrics is None:
        return f"Task '{title}' completed."
    wall_h = round(metrics.wall_clock_seconds / 3600, 1)
    active_h = round(metrics.active_runtime_seconds / 3600, 1)
    turns = str(metrics.turns) if metrics.turns else "n/a"
    return (
        f"Task '{title}' completed.\n\n"
        f"Active effort: {active_h}h across {metrics.stints} stint(s) "
        f"({turns} turns, {metrics.tool_calls} tool-calls)\n"
        f"Wall-clock: {wall_h}h\n"
        f"Revisions: {metrics.revision_count} "
        f"({metrics.qa_fails} QA / {metrics.pr_fails} PR)\n"
        f"Cost: ${round(metrics.cost_usd, 2)}"
    )


# =============================================================================
# Deferred after-commit work — transactional outbox (F107)
# =============================================================================
# `deliver`/`_persist_and_deliver` run inside the caller's open transaction:
# the notification row is flushed but not committed. Publishing the
# NOTIFICATION_SENT event to the Redis bus *before* the commit created
# phantom notifications — a commit failure (DB hiccup, constraint, asyncpg
# error) rolled the row back while connected WebSocket clients had already
# received a push for an id that no longer existed. The fix defers the bus
# publish to the session's `after_commit` so a rollback drops the pending
# event: the row is durable by the time the event fires. The same queue also
# carries the outbound Telegram send (see `_notify_telegram`) — any
# network-adjacent side effect that must not hold the transaction open rides
# this mechanism.
#
# The pending work and the scheduled drain tasks live on `session.info` so
# they are scoped to the session's lifetime (no module-global state, no
# cross-request leak). A sync `after_commit` listener schedules the async
# drain via `asyncio.create_task` (the listener runs synchronously inside
# `await AsyncSession.commit()` on the loop thread, so the running loop is
# available); `after_rollback` clears the pending queue so a rolled-back
# transaction runs none of it.

_PENDING_WORK_KEY = "_roboco_pending_bus_publishes"
_DRAIN_TASKS_KEY = "_roboco_drain_tasks"
_DRAIN_REGISTERED_KEY = "_roboco_drain_registered"


async def _drain_pending_work(pending: list[Callable[[], Awaitable[None]]]) -> None:
    """Run every deferred after-commit action best-effort once the txn has
    committed. Each callable is independent — one failure does not stop the
    rest — and is expected to be fully exception-safe on its own; the
    try/except here is a defensive backstop, not the primary guard.
    """
    for work in pending:
        try:
            await work()
        except Exception as e:  # best-effort: never break the drain
            _log.warning("Deferred after-commit work failed", error=str(e))


def _schedule_pending_work(session: AsyncSession) -> None:
    """`after_commit` handler: hand the pending work to the running loop.

    Sync listener — runs inside `await AsyncSession.commit()`, so the event
    loop is active. The created task is stashed on the session so callers /
    tests can await it deterministically; in production it is fire-and-forget
    (best-effort, matching the prior try/except semantics).
    """
    pending = session.info.pop(_PENDING_WORK_KEY, None)
    if not pending:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # no running loop — nothing we can do, drop silently
        return
    task = loop.create_task(_drain_pending_work(pending))
    session.info.setdefault(_DRAIN_TASKS_KEY, []).append(task)


def _discard_pending_work(session: AsyncSession) -> None:
    """`after_rollback` handler: a rolled-back txn runs none of it (no phantom)."""
    session.info.pop(_PENDING_WORK_KEY, None)


def defer_after_commit(
    session: AsyncSession, work: Callable[[], Awaitable[None]]
) -> None:
    """Enqueue a zero-arg async callable to run only after the session's
    transaction commits; dropped (never run) on rollback.

    Registers one-shot `after_commit` / `after_rollback` listeners on the
    session the first time it is called for that session; subsequent calls
    just append. The listeners are bound to the session instance and are
    collected with it (no global listener accumulation).
    """
    session.info.setdefault(_PENDING_WORK_KEY, []).append(work)
    if session.info.get(_DRAIN_REGISTERED_KEY):
        return
    session.info[_DRAIN_REGISTERED_KEY] = True

    sync_session = session.sync_session

    @event.listens_for(sync_session, "after_commit")
    def _on_commit(_sync_session: object) -> None:
        _schedule_pending_work(session)

    @event.listens_for(sync_session, "after_rollback")
    def _on_rollback(_sync_session: object) -> None:
        _discard_pending_work(session)


def defer_bus_publish(session: AsyncSession, ev: Event) -> None:
    """Enqueue a bus event to fire only after the session's transaction commits.

    Thin wrapper over `defer_after_commit`: the bus is read fresh at drain
    time (it may have reconnected between deferral and commit); a
    disconnected bus is a silent no-op, matching the prior inline behavior.
    """

    async def _publish() -> None:
        bus = get_event_bus()
        if bus.is_connected():
            await bus.publish(ev)

    defer_after_commit(session, _publish)


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
        """Re-escalate then log ack-required notifications past `expires_at`.

        `NotificationTable.expires_at` existed but nothing acted on it. This
        sweep surfaces notifications that have become stale. For an
        ack-required row still unacked past the threshold, the recipient's
        up-role (the PM's PM, or the CEO) is re-notified BEFORE the row is
        logged as expired — so an inattentive PM can't both miss a blocker
        and prevent anyone upstream from seeing it. Non-ack-required rows
        and already-acked rows are not re-escalated. We log rather than
        auto-cancel because the notification is the record; rewriting
        status would be ambiguous. Returns the count of stale unacked items.
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

        # Python mirror of the SQL `requires_ack.is_(True)` predicate: defense
        # in depth so a future query change can't silently re-escalate
        # non-ack-required rows.
        unacked = [
            n
            for n in stale
            if n.requires_ack and not self._notification_is_fully_acked(n)
        ]
        for n in unacked:
            await self._re_escalate_unacked(n)
            self._log_expired_notification(n)
        return len(unacked)

    async def _re_escalate_unacked(self, n: NotificationTable) -> None:
        """Re-send an unacked ack-required notification to each non-acking
        recipient's up-role before expiry. Best-effort: a missing chain,
        target, or a dedup-suppressed re-fire is logged-and-skipped, never
        raises — the expiry log still fires. The loop-prone dedup guard in
        `_persist_and_deliver` caps repeat re-escalations within the 60s
        window so a tight sweep loop can't flood the upstream role."""
        acked = {str(a) for a in (n.acked_by or [])}
        for recipient_id in n.to_agents or []:
            if str(recipient_id) in acked:
                continue
            await self._re_escalate_recipient(n, cast("UUID", recipient_id))

    async def _re_escalate_recipient(
        self, n: NotificationTable, recipient_id: UUID
    ) -> None:
        """Resolve one recipient's up-role and re-fire the escalation."""
        recipient = await self._get_agent_by_id(recipient_id)
        if not recipient or not recipient.slug:
            return
        target_slug = get_escalation_target(recipient.slug)
        if not target_slug:
            return
        target = await self._get_agent_by_slug(target_slug)
        if not target:
            return
        notification = NotificationTable(
            type=NotificationType.BLOCKER_ESCALATION,
            priority=NotificationPriority.HIGH,
            from_agent=cast("UUID", n.from_agent),
            to_agents=[target.id],
            subject=f"Re-escalation (unacked): {n.subject[:140]}",
            body=(
                f"A notification addressed to {recipient.slug} was not "
                f"acknowledged before its expiry.\n\n"
                f"Original subject: {n.subject}\n\n"
                "Please review and act on the underlying issue."
            ),
            related_task_id=cast("UUID | None", n.related_task_id),
            requires_ack=ACK_REQUIRED_BY_TYPE[NotificationType.BLOCKER_ESCALATION],
            read_by=[],
            acked_by=[],
        )
        try:
            await self._persist_and_deliver(notification)
        except Exception as e:
            self.log.warning(
                "Re-escalation deliver failed",
                notification_id=str(n.id),
                target_slug=target_slug,
                error=str(e),
            )

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

        # Defer the ACK event to the session's after_commit (mirror ``deliver``):
        # the row state above is only flushed, not durable, so firing the bus
        # event now would publish an ACK for an acknowledgement that a rollback
        # can still drop. The event is dropped on rollback (no phantom) and fired
        # once the row is durable. Best-effort: a bus-init failure is logged but
        # never propagates — the ack row state is already flushed and the bus is
        # a secondary channel (the row is the durable store).
        try:
            bus = get_event_bus()
            if bus.is_connected():
                defer_bus_publish(
                    self.session,
                    Event(
                        type=EventType.NOTIFICATION_ACKED,
                        data={
                            "notification_id": str(notification_id),
                            "agent_id": str(agent_id),
                            "ack_type": ack_type,
                        },
                    ),
                )
        except Exception as e:
            self.log.warning(
                "Failed to defer ACK bus publish",
                notification_id=str(notification_id),
                error=e.__class__.__name__,
            )

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
    ) -> dict[str, Any] | None:
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
    ) -> dict[str, Any]:
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
                f"Task {task_display(task_title, task_id)} has been BLOCKED by "
                f"{blocker_name}.\n\n"
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
                f"Task {task_display(task, task_id)} documentation is complete "
                "and ready for final review.\n\nPlease review and complete the task."
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
                f"Task {task_display(task, task_id)} has been submitted for PM "
                f"review.\n\nNotes: {notes or 'None'}\n\n"
                "Please review and complete the task."
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
                f"Task {task_display(task, task_id)} was rejected by CEO and "
                f"requires revision.\n\nReason: {notes}\n\n"
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

        body = (
            f"Task {task_display(task, task_id)} escalated by "
            f"{escalator.slug}.\n\nReason: {reason}"
        )
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

    async def _send_telegram_deferred(
        self,
        *,
        text: str,
        reply_markup: dict[str, Any] | None,
        disable_link_preview: bool = False,
    ) -> None:
        """Shared best-effort deferred-send plumbing behind every Telegram DM
        this service issues (``_notify_telegram``, ``notify_ceo_of_queue_item``).

        Degrades to a no-op unless ``telegram_enabled`` is armed and
        credentials are stored. Credentials are fetched now (a fast DB read
        on the open session); the actual network send is deferred via
        ``defer_after_commit`` so a slow Telegram Bot API call can't hold the
        caller's open transaction for up to ``telegram_timeout_seconds``.
        Never raises into the caller — a credentials/network failure only
        logs. ``text`` is sent with HTML ``parse_mode``; callers are
        responsible for escaping every dynamic value they interpolated into
        it (``_esc``).
        """
        from roboco.config import settings

        if not settings.telegram_enabled:
            return
        from roboco.services.telegram_client import build_telegram_client
        from roboco.services.telegram_credentials import (
            get_telegram_credentials_service,
        )

        try:
            creds = await get_telegram_credentials_service(self.session).get_decrypted()
        except Exception as exc:  # best-effort — never block the producer
            _log.warning("telegram_notify_failed", error=str(exc))
            return

        timeout = settings.telegram_timeout_seconds

        async def _send() -> None:
            client = None
            try:
                client = build_telegram_client(creds, timeout=timeout)
                result = await client.send_message(
                    text,
                    reply_markup=reply_markup,
                    parse_mode="HTML",
                    disable_link_preview=disable_link_preview,
                )
                if not result.sent:
                    _log.warning("telegram_notify_skip", detail=result.detail)
            except Exception as exc:  # best-effort — never break the drain
                _log.warning("telegram_notify_failed", error=str(exc))
            finally:
                if client is not None:
                    with contextlib.suppress(Exception):
                        await client.close()

        defer_after_commit(self.session, _send)

    async def _notify_telegram(
        self, *, task_id: UUID, subject: str, actionable: bool = False
    ) -> None:
        """Best-effort Telegram DM to the CEO alongside an in-app notification.

        The message carries a panel deep-link (named "Open in panel", link
        preview disabled so the card never swallows the chat) when
        ``panel_base_url`` is set.

        ``actionable=True`` (escalation only — V1's completion send never
        expands beyond link-only, and no new call site is added here) also
        attaches an Approve/Reject/Open inline keyboard (V2, gated separately
        by ``telegram_inbound_enabled`` — with it off the buttons render but
        the bot never polls for the tap, so they're harmlessly inert; the
        plain-text link still works either way).
        """
        from roboco.config import settings

        text = f"<b>{_esc(subject)}</b>"
        if settings.panel_base_url:
            link = f"{settings.panel_base_url.rstrip('/')}/tasks/{str(task_id)[:8]}"
            text += f'\n<a href="{_esc_attr(link)}">Open in panel</a>'
        reply_markup = None
        if actionable:
            from roboco.services.telegram_inbound import build_action_keyboard

            reply_markup = build_action_keyboard("task", str(task_id)[:8])

        await self._send_telegram_deferred(
            text=text, reply_markup=reply_markup, disable_link_preview=True
        )

    async def notify_ceo_of_queue_item(
        self, *, kind: str, id8: str, extra: str = "", title: str
    ) -> None:
        """Best-effort push DM at the moment a held draft becomes CEO-
        actionable — release proposals, X drafts, video posts, and roadmap
        items used to land in the approval queue silently, with no ping
        until the CEO happened to run ``/queue``. Reuses the exact styled
        item line and Approve/Reject/Open keyboard ``/queue`` itself renders
        (``telegram_inbound.render_queue_item_text`` / ``build_action_keyboard``
        — one renderer, two callers), and the same degrade-to-no-op contract
        as ``_notify_telegram``: a credentials/network failure only logs,
        never raises into the originating engine.
        """
        from roboco.services.telegram_inbound import (
            build_action_keyboard,
            render_queue_item_text,
        )

        text = render_queue_item_text(kind, id8, extra, title)
        reply_markup = build_action_keyboard(kind, id8, extra)
        await self._send_telegram_deferred(text=text, reply_markup=reply_markup)

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
                f"Task {task_display(task, task_id)} requires CEO approval for "
                f"completion.\n\nEscalated by: {escalator_role}\n"
                f"Notes: {notes or 'None'}\n\n"
                "Use /ceo-approve or /ceo-reject to respond."
            ),
            related_task_id=task_id,
            requires_ack=ACK_REQUIRED_BY_TYPE[NotificationType.APPROVAL],
        )
        await self._persist_and_deliver(notification)
        await self._notify_telegram(
            task_id=task_id, subject=notification.subject, actionable=True
        )

    async def notify_ceo_of_completion(self, *, task: TaskTable, task_id: UUID) -> None:
        """CEO-facing completion notification with the granular effort breakdown.

        Replaces the coarse "completed in Xh" wall-clock figure with real effort
        vs wall-clock + turns/stints/revisions/cost from the per-task metrics.
        Best-effort: a metrics or delivery failure must never block completion.
        """
        ceo = await self._get_ceo_agent()
        if not ceo:
            return
        from roboco.services.metrics import MetricsService

        try:
            metrics = await MetricsService(self.session).get_task_metrics(task_id)
        except Exception:  # metrics are best-effort — degrade to wall-clock-only
            metrics = None
        from_agent = cast("UUID", task.assigned_to) if task.assigned_to else ceo.id
        notification = NotificationTable(
            type=NotificationType.ALERT,
            priority=NotificationPriority.NORMAL,
            from_agent=from_agent,
            to_agents=[ceo.id],
            subject=f"Completed: {(task.title or 'Untitled')[:60]}",
            body=_format_completion_body(task, metrics),
            related_task_id=task_id,
            requires_ack=ACK_REQUIRED_BY_TYPE[NotificationType.ALERT],
        )
        await self._persist_and_deliver(notification)
        await self._notify_telegram(task_id=task_id, subject=notification.subject)

    async def notify_ceo_of_brand_voice_unset(self) -> None:
        """One-time nudge (see ``XEngine._maybe_nudge_brand_voice``): no
        ``company_goals.brand_voice`` sample is set, so X/video drafts are
        running on the generic house voice. Informational, no ack — the CEO
        can ignore it and drafting keeps working exactly as before.
        """
        ceo = await self._get_ceo_agent()
        if not ceo:
            return
        notification = NotificationTable(
            type=NotificationType.BROADCAST,
            priority=NotificationPriority.NORMAL,
            from_agent=ceo.id,
            to_agents=[ceo.id],
            subject="Set a brand voice for sharper X/video drafts",
            body=(
                "X posts and video captions are drafting on RoboCo's generic "
                "house voice — no sample of yours is set yet. Add one in "
                "Settings -> Business -> Goals -> Brand voice and every "
                "future draft will read more like you wrote it."
            ),
            requires_ack=ACK_REQUIRED_BY_TYPE[NotificationType.BROADCAST],
            read_by=[],
            acked_by=[],
        )
        await self._persist_and_deliver(notification)

    async def notify_auditor_of_rework(
        self,
        *,
        task: TaskTable,
        task_id: UUID,
        reason: str,
        actor_agent_id: UUID | None = None,
        actor_role: str | None = None,
    ) -> None:
        """Auditor-targeted alert when a task enters needs_revision.

        The orchestrator's ``_dispatch_audit_work`` watches for notifications of
        type ``ALERT`` whose ``to_agents`` include the auditor and spawns the
        auditor with a quality-alert prompt. This producer reactivates that
        reactive dispatch path at the QA-fail / rework chokepoints.
        """
        auditor = await self._get_auditor_agent()
        if not auditor:
            return

        actor = await self._get_agent_by_id(actor_agent_id) if actor_agent_id else None
        from_agent = actor_agent_id if actor_agent_id is not None else auditor.id
        title = task.title or "Untitled task"
        role_label = actor_role or (actor.role if actor else "system")

        body_lines = [
            f"Task {task_display(title, task_id)} entered needs_revision.",
            "",
            f"Reason: {reason}",
            f"Actor role: {role_label}",
        ]
        if actor:
            body_lines.append(f"Actor: {actor.slug}")

        notification = NotificationTable(
            type=NotificationType.ALERT,
            priority=NotificationPriority.HIGH,
            from_agent=from_agent,
            to_agents=[auditor.id],
            subject=f"Rework alert: {title[:40]}",
            body="\n".join(body_lines),
            related_task_id=task_id,
            requires_ack=ACK_REQUIRED_BY_TYPE[NotificationType.ALERT],
            read_by=[],
            acked_by=[],
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
        """Find the CEO agent (org-wide singleton; earliest-created if many).

        Delegates to the shared `get_agent_by_role` helper — a plain
        one-or-none raises MultipleResultsFound if a second CEO-role row ever
        exists, so it pins to the earliest-created (the canonical seeded CEO)
        instead.
        """
        return await get_agent_by_role(self.session, AgentRole.CEO)

    async def _get_auditor_agent(self) -> AgentTable | None:
        """Find the auditor agent (org-wide; earliest-created if many)."""
        return await get_agent_by_role(self.session, AgentRole.AUDITOR)

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
            subject=notification.subject,
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
        a SQL-friendly predicate against PostgreSQL array columns. The SQL
        ``limit`` is therefore NOT applied for that branch — applying it before
        the Python filter would let a window of newer fully-acked rows mask
        older unacked ones the operator still needs to act on. We fetch the
        ack-required set ordered newest-first, drop the fully-acked rows in
        Python, then slice to ``limit``.
        """
        query = select(NotificationTable)
        if pending_ack_only:
            query = query.where(NotificationTable.requires_ack.is_(True))
        if type_filter:
            query = query.where(NotificationTable.type == type_filter)
        query = query.order_by(NotificationTable.timestamp.desc())
        if not pending_ack_only:
            query = query.limit(limit)

        result = await self.session.execute(query)
        notifications = list(result.scalars().all())
        if pending_ack_only:
            unacked = [
                n
                for n in notifications
                if not all(t in n.acked_by for t in n.to_agents)
            ]
            return unacked[:limit]
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
        # Drop the per-recipient Redis dedup key so a post-ack re-send of the
        # same notification is not suppressed by a stale 60s window. The key
        # is per (type, sender, recipient, task, subject); only loop-prone
        # types carry one, and clear_dedup_key is a no-op fail-open for the
        # rest. Best-effort: a Redis miss never blocks the ack.
        await clear_dedup_key(
            ntype=notification.type,
            from_agent=cast("UUID", notification.from_agent),
            recipient=agent_id,
            related_task_id=cast("UUID | None", notification.related_task_id),
            subject=notification.subject,
        )
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
