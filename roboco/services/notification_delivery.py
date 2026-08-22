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
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, ClassVar, Literal, cast
from uuid import UUID

import structlog
from sqlalchemy import CursorResult, and_, case, event, func, not_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.agents_config import (
    get_escalation_target,
    get_pm_for_agent,
    get_pm_for_team,
)
from roboco.config import settings
from roboco.db.tables import AgentTable, NotificationTable, PitchTable, TaskTable
from roboco.events import Event, EventType, get_event_bus
from roboco.foundation.policy.communications import (
    ACK_REQUIRED_BY_TYPE,
    ReescalationPolicy,
    reescalation_decision,
)
from roboco.models.base import (
    AgentRole,
    NotificationPriority,
    NotificationType,
    TaskStatus,
)
from roboco.services.base import BaseService, NotFoundError
from roboco.services.notification_dedup import (
    all_recipients_recently_notified,
    clear_dedup_key,
    duplicate_unacked_notification_exists,
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
        # after_commit also fires on SAVEPOINT release (`begin_nested()`
        # exit), before the real commit — draining there would reintroduce
        # the phantom-notification bug this outbox exists to prevent.
        # `get_transaction()` (the root txn) is NOT a usable discriminator:
        # it stays non-None at a savepoint release too (verified live —
        # SQLAlchemy dispatches before closing the just-committed
        # SessionTransaction, so `session._transaction` hasn't reverted
        # yet). `get_nested_transaction()` IS: only non-None while a
        # savepoint is the active transaction, which is exactly the frame
        # this event fires in for a savepoint release.
        if sync_session.get_nested_transaction() is not None:
            return
        _schedule_pending_work(session)

    @event.listens_for(sync_session, "after_rollback")
    def _on_rollback(_sync_session: object) -> None:
        # Savepoint rollback keeps pending work: every registration site
        # flushes before deferring, so work registered inside a savepoint
        # that then rolls back is unreachable in practice. Same
        # discriminator as `_on_commit` above.
        if sync_session.get_nested_transaction() is not None:
            return
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
            reescalation_count=n.reescalation_count,
        )

    def _log_permanently_unacked(self, n: NotificationTable) -> None:
        """One-time terminal log: `n` hit the re-escalation cap and will never
        be re-escalated again (fires exactly once, on the tick of its last
        permitted re-escalation). Carries both totals so "seen and ignored"
        (delivered > 0, recipients just never acked) reads distinctly from
        "route never worked" (delivered == 0 despite every attempt — a
        broken escalation chain, e.g. no configured up-role)."""
        self.log.warning(
            "Notification permanently unacked — re-escalation cap reached",
            notification_id=str(n.id),
            type=n.type.value if n.type else None,
            priority=n.priority.value if n.priority else None,
            recipient_count=len(n.to_agents or []),
            ack_count=len(n.acked_by or []),
            reescalation_count=n.reescalation_count,
            reescalation_delivered_count=n.reescalation_delivered_count,
        )

    async def resolve_terminal_task_escalations(self) -> int:
        """Auto-ack every requires_ack notification whose related task has
        gone terminal (completed/cancelled): the escalation's premise ("this
        needs your attention") no longer holds once the task it names is
        done, so no recipient can meaningfully act on it.

        Runs every sweep tick, independent of `expires_at`: without this, an
        unacked escalation whose task finished out from under it keeps
        looking "pending" (feeding both `EvidenceRepo.list_pending_notifications`,
        which drives `i_am_idle`'s soft-block, and `list_system_notifications`,
        which drives the escalation dispatcher) and keeps re-escalating up the
        chain via `sweep_expired_notifications` every backoff window, all the
        way to the CEO, until its own `expires_at` (default 2 days), for work
        that is already done. Best-effort per row, mirroring
        `sweep_expired_notifications`'s per-row commit/rollback isolation: one
        bad row is logged and skipped, never aborting the rest. Returns the
        count actually resolved.
        """
        result = await self.session.execute(
            select(NotificationTable)
            .join(TaskTable, TaskTable.id == NotificationTable.related_task_id)
            .where(
                NotificationTable.requires_ack.is_(True),
                TaskTable.status.in_((TaskStatus.COMPLETED, TaskStatus.CANCELLED)),
            )
        )
        candidates = list(result.scalars().all())
        row_ids = [n.id for n in candidates if not self._notification_is_fully_acked(n)]
        now = datetime.now(UTC)
        resolved = 0
        for nid in row_ids:
            try:
                n = await self.session.get(NotificationTable, nid)
                if n is None or self._notification_is_fully_acked(n):
                    continue
                self._auto_ack_terminal_task_notification(n, now)
                await self.session.commit()
                resolved += 1
                self.log.info(
                    "Auto-resolved notification: related task is terminal",
                    notification_id=str(nid),
                    related_task_id=str(n.related_task_id),
                )
            except Exception as e:
                await self.session.rollback()
                self.log.warning(
                    "Terminal-task notification auto-resolve failed",
                    notification_id=str(nid),
                    error=str(e),
                )
        return resolved

    @staticmethod
    def _auto_ack_terminal_task_notification(
        n: NotificationTable, now: datetime
    ) -> None:
        """Ack every not-yet-acked recipient of `n`: its related task is
        done, so the escalation is resolved by definition rather than by a
        human decision. Mirrors `acknowledge_for_recipient`'s acked_by/
        acked_at write (minus the dedup-key clear, which is per-recipient
        Redis state a bulk sweep pass has no single recipient to key off)."""
        acked = set(n.acked_by or [])
        newly = [r for r in cast("list[UUID]", n.to_agents or []) if r not in acked]
        if not newly:
            return
        n.acked_by = [*n.acked_by, *newly]
        n.acked_at = {**n.acked_at, **{str(r): now.isoformat() for r in newly}}

    async def sweep_expired_notifications(self) -> int:
        """Re-escalate (per a backoff schedule) then log ack-required
        notifications past `expires_at`.

        `NotificationTable.expires_at` existed but nothing acted on it. This
        sweep surfaces notifications that have become stale. For an
        ack-required row still unacked past the threshold, the recipient's
        up-role (the PM's PM, or the CEO) is re-notified — but only when
        `reescalation_decision` says it's due: the first re-escalation fires
        immediately at expiry, each one after that backs off exponentially
        (`notification_reescalation_base_seconds`, doubling, capped at 24h),
        and past `notification_max_reescalations` the row is left alone for
        good. Without the schedule, a static pile of stale rows re-escalated
        on every ~1min sweep tick forever. Non-ack-required rows and
        already-acked rows are never re-escalated. We log rather than
        auto-cancel because the notification is the record; rewriting status
        would be ambiguous. Returns the count of stale unacked items (not just
        the ones actually re-escalated this tick).
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
        # Per-row commit scope (the sweep owns a dedicated session — the only
        # caller is the orchestrator's _run_sweep). One tick-wide transaction
        # held every claimed row's lock until the final commit, so a
        # recipient's concurrent mark-read UPDATE on an already-claimed row
        # sat blocked until it hit the 60s lock_timeout. Committing per row
        # also isolates one row's failure from the rest of the tick.
        #
        # A root `rollback()` expires every object in the session, so a
        # bad row can't stay a live ORM instance across the except block
        # (`str(n.id)` would need an async lazy-refresh in sync context and
        # raise MissingGreenlet) — and every LATER row in `unacked` would be
        # expired too, breaking the whole tick on one bad row. Snapshot ids
        # up front and re-fetch each via `session.get` (async-safe even
        # post-expiry) instead of iterating the ORM instances directly.
        row_ids = [n.id for n in unacked]
        for nid in row_ids:
            try:
                n = await self.session.get(NotificationTable, nid)
                if n is None:
                    continue
                await self._maybe_reescalate(n, now)
                await self.session.commit()
            except Exception as e:
                await self.session.rollback()
                self.log.warning(
                    "Re-escalation failed; row skipped this tick",
                    notification_id=str(nid),
                    error=str(e),
                )
        return len(unacked)

    async def _maybe_reescalate(self, n: NotificationTable, now: datetime) -> None:
        """Re-escalate `n` only when its backoff schedule says it's due.

        Neither of `_persist_and_deliver`'s two dedup guards backstops a
        concurrent double-sweep here. The Redis 60s guard is a structural
        no-op: `BLOCKER_ESCALATION` — the type every re-escalation
        notification is created as — is not in `_LOOP_PRONE_TYPES`
        (notification_dedup.py), so that guard returns False unconditionally
        for it. The DB purpose-dedup guard WOULD otherwise apply to
        `BLOCKER_ESCALATION` (it's ACK_REQUIRED_BY_TYPE), but
        `_re_escalate_recipient` deliberately passes
        `bypass_purpose_dedup=True` so a legitimate repeat re-escalation
        against a still-unacked prior row is never silently dropped. The
        real guard against double-delivery is `_claim_reescalation_slot`'s
        compare-and-set: it must succeed BEFORE any delivery is attempted,
        so two sweep ticks racing the same row can never both deliver.
        """
        decision = reescalation_decision(
            now=now,
            expires_at=cast("datetime", n.expires_at),
            count=n.reescalation_count,
            last_reescalated_at=n.last_reescalated_at,
            policy=ReescalationPolicy(
                base_seconds=settings.notification_reescalation_base_seconds,
                max_reescalations=settings.notification_max_reescalations,
            ),
        )
        if decision != "due":
            return  # "wait": not due yet; "capped": already logged + done
        if not await self._claim_reescalation_slot(n, now):
            return  # another sweep tick already claimed this attempt
        # Commit the claim immediately: the row lock the CAS took is released
        # before any delivery work (holding it across delivery is what starved
        # concurrent mark-read/ack UPDATEs into lock_timeout), and the burned
        # slot is durable — a delivery failure rolling it back would un-burn
        # the attempt and re-open the retry-forever loop the cap exists for.
        await self.session.commit()
        delivered = await self._re_escalate_unacked(n)
        n.reescalation_delivered_count += delivered
        if n.reescalation_count >= settings.notification_max_reescalations:
            self._log_permanently_unacked(n)
        else:
            self._log_expired_notification(n)

    async def _claim_reescalation_slot(
        self, n: NotificationTable, now: datetime
    ) -> bool:
        """Compare-and-set claim on `n`'s attempt slot, BEFORE any delivery.

        A guarded `UPDATE ... WHERE id = :id AND reescalation_count = :n`:
        Postgres takes a row lock, so a concurrent claim against the same
        `reescalation_count` value blocks until this one commits, then loses
        (0 rows matched — the count already moved). Only the winner proceeds
        to `_re_escalate_unacked`. The slot is consumed (count bumped) even
        though delivery hasn't happened yet: a transient delivery failure
        still burns an attempt, which is what stops a permanently-broken
        escalation chain from looping forever rather than eventually capping.
        """
        result = await self.session.execute(
            update(NotificationTable)
            .where(
                NotificationTable.id == n.id,
                NotificationTable.reescalation_count == n.reescalation_count,
            )
            .values(
                reescalation_count=n.reescalation_count + 1, last_reescalated_at=now
            )
            .execution_options(synchronize_session=False)
        )
        # UPDATE always yields a CursorResult (has `.rowcount`); `execute`'s
        # declared return type is the generic `Result` supertype, so peel it.
        claimed = cast("CursorResult[Any]", result).rowcount == 1
        if claimed:
            # Mirror the winning UPDATE onto the in-memory object so the rest
            # of this tick (and the eventual `reescalation_delivered_count`
            # flush) sees consistent state. SQLAlchemy will re-flush these
            # same two columns with the caller's next commit — a harmless
            # no-op re-write of the value we just committed, not a bug.
            n.reescalation_count += 1
            n.last_reescalated_at = now
        return claimed

    async def _re_escalate_unacked(self, n: NotificationTable) -> int:
        """Re-send an unacked ack-required notification to each non-acking
        recipient's up-role. Best-effort: a missing chain or target is
        logged-and-skipped, never raises. Returns how many recipients were
        actually re-notified — used to distinguish a broken escalation chain
        (0 delivered despite an attempt) from one that works but is ignored."""
        acked = {str(a) for a in (n.acked_by or [])}
        delivered = 0
        for recipient_id in n.to_agents or []:
            if str(recipient_id) in acked:
                continue
            if await self._re_escalate_recipient(n, cast("UUID", recipient_id)):
                delivered += 1
        return delivered

    async def _re_escalate_recipient(
        self, n: NotificationTable, recipient_id: UUID
    ) -> bool:
        """Resolve one recipient's up-role and re-fire the escalation.
        Returns True iff it was actually persisted+delivered."""
        recipient = await self._get_agent_by_id(recipient_id)
        if not recipient or not recipient.slug:
            return False
        target_slug = get_escalation_target(recipient.slug)
        if not target_slug:
            return False
        target = await self._get_agent_by_slug(target_slug)
        if not target:
            return False
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
            # Stamp the same TTL a first-send blocker gets so this row is
            # itself swept + can be re-escalated further up the chain — an
            # un-stamped row would live forever and dead-end the ladder here.
            expires_at=(
                datetime.now(UTC) + timedelta(hours=settings.notification_ack_ttl_hours)
                if settings.notification_ack_ttl_hours > 0
                else None
            ),
        )
        try:
            # Savepoint: a mid-flush failure otherwise poisons the session for
            # every remaining recipient of this notification (and the caller's
            # commit), turning one bad delivery into a whole-row failure.
            # Every attempt at this recipient rebuilds an identical
            # BLOCKER_ESCALATION while the prior one is still unacked BY
            # DEFINITION (that's why we're re-escalating) — bypass DB
            # purpose-dedup so attempt 2+ isn't silently suppressed right
            # after `_claim_reescalation_slot` already burned the attempt
            # slot. The CAS claim upstream, not this dedup, is what prevents
            # a genuine double-delivery.
            async with self.session.begin_nested():
                return await self._persist_and_deliver(
                    notification, bypass_purpose_dedup=True
                )
        except Exception as e:
            self.log.warning(
                "Re-escalation deliver failed",
                notification_id=str(n.id),
                target_slug=target_slug,
                error=str(e),
            )
            return False

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
        # SQL COUNT aggregates — never materialize the full row set (this is
        # hit on every panel-bell/Telegram cockpit poll).
        unread_case = case(
            (not_(NotificationTable.read_by.contains([agent_id])), 1), else_=0
        )
        pending_ack_case = case(
            (
                and_(
                    NotificationTable.requires_ack.is_(True),
                    not_(NotificationTable.acked_by.contains([agent_id])),
                ),
                1,
            ),
            else_=0,
        )
        query = select(
            func.count().label("total"),
            func.coalesce(func.sum(unread_case), 0).label("unread"),
            func.coalesce(func.sum(pending_ack_case), 0).label("pending_ack"),
        ).where(NotificationTable.to_agents.contains([agent_id]))

        result = await self.session.execute(query)
        row = result.one()

        return {
            "total": int(row.total),
            "unread": int(row.unread),
            "pending_ack": int(row.pending_ack),
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
        self,
        *,
        kind: str,
        id8: str,
        extra: str = "",
        title: str,
        related_task_id: UUID | None = None,
    ) -> None:
        """Push DM plus in-app notification at the moment a held draft
        becomes CEO-actionable: release proposals, X drafts, video posts,
        roadmap items, and every Board Program's per-item proposals (Mirror,
        Pest Control, Spackle, Scales, Dogfood, ...) all route through this
        one chokepoint. Telegram reuses the exact styled item line and
        Approve/Reject/Open keyboard ``/queue`` itself renders
        (``telegram_inbound.render_queue_item_text`` /
        ``build_action_keyboard``, one renderer shared by both callers) and
        keeps its own degrade-to-no-op contract unchanged: a
        credentials/network failure only logs, never raises into the
        originating engine.

        The in-app row is shaped like ``notify_ceo_of_escalation`` /
        ``notify_ceo_of_pitch`` (APPROVAL/HIGH, requires_ack, since a queue
        item genuinely needs a CEO decision the same as those do). It stays
        independent of the Telegram half in both directions: a persist
        failure is caught and logged here instead of propagating (which
        would otherwise skip the Telegram send below), and a Telegram
        failure can never undo the already-flushed row. A missing
        ``AgentTable`` CEO row only skips the in-app half; Telegram sends off
        stored credentials, not that row, so its behavior stays exactly what
        it was before this method persisted anything.

        ``related_task_id`` is every caller's own held/exploration task
        (the release proposal, the X/video draft, or the queue program's
        shared exploration task the item lives on); without it the row can
        never resolve: ``resolve_terminal_task_escalations``'s JOIN needs it
        to auto-ack once the underlying decision is made (the task goes
        terminal), and ``expires_at`` (stamped below from
        ``notification_ack_ttl_hours``) backstops the rest via the normal
        re-escalation/permanently-unacked path, same as every other
        ack-required notification in this file. A caller with no resolvable
        task (none today) simply omits it and falls back to that same
        expires_at-only backstop.
        """
        from roboco.services.telegram_inbound import (
            build_action_keyboard,
            render_queue_item_text,
        )

        ceo = await self._get_ceo_agent()
        if ceo is not None:
            label = kind.replace("_", " ")
            notification = NotificationTable(
                type=NotificationType.APPROVAL,
                priority=NotificationPriority.HIGH,
                from_agent=ceo.id,
                to_agents=[ceo.id],
                subject=f"{label.title()} awaiting review: {title[:100]}",
                body=(
                    f"A new {label} item is ready for your review "
                    f"(ref {id8}{f':{extra}' if extra else ''}).\n\n"
                    f"{title}\n\n"
                    "Approve or reject it from the Telegram push, or review "
                    "it in the panel's queue."
                ),
                related_task_id=related_task_id,
                requires_ack=ACK_REQUIRED_BY_TYPE[NotificationType.APPROVAL],
                expires_at=(
                    datetime.now(UTC)
                    + timedelta(hours=settings.notification_ack_ttl_hours)
                    if settings.notification_ack_ttl_hours > 0
                    else None
                ),
            )
            try:
                # Every item of one proposal cycle shares (from_agent=ceo,
                # type=APPROVAL, related_task_id, to_agents=[ceo]): the
                # SAME shared exploration task for a multi-item cycle; the
                # purpose-dedup guard keys ONLY on that tuple (subject/body
                # not compared), so without the bypass every item after the
                # first still-unacked one would be silently dropped in-app.
                # Each is a genuinely distinct thing to review, not a resend.
                await self._persist_and_deliver(notification, bypass_purpose_dedup=True)
            except Exception as exc:
                self.log.warning(
                    "queue_item in-app notify failed (best-effort)",
                    kind=kind,
                    error=str(exc),
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

    async def notify_ceo_of_pitch(self, *, pitch: PitchTable) -> None:
        """Best-effort CEO nudge the moment a Board pitch is proposed —
        without it a pitch sits in the queue with no signal until the CEO
        happens to open the panel. Shaped like ``notify_ceo_of_escalation``
        (APPROVAL/HIGH to the CEO + a Telegram push), but a pitch isn't a
        task: ``related_task_id`` stays unset and the Telegram link points
        at the panel's Pitches tab instead of a task deep-link.
        """
        ceo = await self._get_ceo_agent()
        if not ceo:
            return
        cells = ", ".join(pitch.target_cells)
        problem_line = pitch.problem.strip().split("\n", 1)[0][:200]
        notification = NotificationTable(
            type=NotificationType.APPROVAL,
            priority=NotificationPriority.HIGH,
            from_agent=pitch.created_by,
            to_agents=[ceo.id],
            subject=f"Pitch awaiting review: {pitch.title}",
            body=(
                f"Slug: {pitch.slug}\nTarget cells: {cells}\n\n"
                f"{problem_line}\n\n"
                "Review it in the panel's Pitches tab (Business page)."
            ),
            requires_ack=ACK_REQUIRED_BY_TYPE[NotificationType.APPROVAL],
        )
        await self._persist_and_deliver(notification)
        text = f"<b>{_esc(notification.subject)}</b>"
        if settings.panel_base_url:
            link = f"{settings.panel_base_url.rstrip('/')}/business?tab=pitches"
            text += f'\n<a href="{_esc_attr(link)}">Open in panel</a>'
        await self._send_telegram_deferred(
            text=text, reply_markup=None, disable_link_preview=True
        )

    async def notify_ceo_of_periscope_brief(
        self, *, task: TaskTable, task_id: UUID, headline: str
    ) -> None:
        """Best-effort CEO nudge the moment a Periscope market brief lands.

        A report, not a queue item: no approve/reject verb exists for it, so
        this reuses ``notify_ceo_of_queue_item``'s styled-text SHAPE
        (``render_queue_item_text`` — "periscope" is a display-only
        ``_KIND_DISPLAY`` entry, never added to ``_VALID_KINDS``/the
        approve-reject callback surface) but skips ``build_action_keyboard``
        entirely: no callback exists for a kind ``parse_callback`` never
        accepts, so no button is ever rendered to tap. The in-app
        notification is a normal ALERT (mirrors ``notify_ceo_of_completion``),
        not an APPROVAL — nothing here needs a CEO decision through a queue.
        """
        ceo = await self._get_ceo_agent()
        if not ceo:
            return
        from_agent = cast("UUID", task.assigned_to) if task.assigned_to else ceo.id
        notification = NotificationTable(
            type=NotificationType.ALERT,
            priority=NotificationPriority.NORMAL,
            from_agent=from_agent,
            to_agents=[ceo.id],
            subject=f"Market brief: {headline[:100]}",
            body=(
                "The Head of Marketing filed this week's market-research "
                f"brief.\n\n{headline}\n\n"
                "Read the full brief in the panel's Market Briefs tab "
                "(Business page)."
            ),
            related_task_id=task_id,
            requires_ack=ACK_REQUIRED_BY_TYPE[NotificationType.ALERT],
        )
        await self._persist_and_deliver(notification)
        from roboco.services.telegram_inbound import render_queue_item_text

        text = render_queue_item_text("periscope", str(task_id)[:8], "", headline)
        if settings.panel_base_url:
            link = f"{settings.panel_base_url.rstrip('/')}/business?tab=market-briefs"
            text += f'\n<a href="{_esc_attr(link)}">Open in panel</a>'
        await self._send_telegram_deferred(
            text=text, reply_markup=None, disable_link_preview=True
        )

    async def notify_ceo_of_sentinel_report(
        self, *, task: TaskTable, task_id: UUID, headline: str
    ) -> None:
        """Best-effort CEO nudge the moment a Sentinel quality report lands.

        Mirrors ``notify_ceo_of_periscope_brief`` exactly: a report, not a
        queue item — no approve/reject verb exists for it, so this reuses
        ``render_queue_item_text``'s styled-text SHAPE ("sentinel" is a
        display-only ``_KIND_DISPLAY`` entry, never added to
        ``_VALID_KINDS``/the approve-reject callback surface) but skips
        ``build_action_keyboard`` entirely — no callback exists for a kind
        ``parse_callback`` never accepts. A normal ALERT, not an APPROVAL —
        nothing here needs a CEO decision through a queue. The Auditor stays
        silent to agents throughout (spec §4's "Auditor boundary") — this
        notification goes to the CEO only.
        """
        ceo = await self._get_ceo_agent()
        if not ceo:
            return
        from_agent = cast("UUID", task.assigned_to) if task.assigned_to else ceo.id
        notification = NotificationTable(
            type=NotificationType.ALERT,
            priority=NotificationPriority.NORMAL,
            from_agent=from_agent,
            to_agents=[ceo.id],
            subject=f"Quality report: {headline[:100]}",
            body=(
                "The Auditor filed this week's state-of-quality report.\n\n"
                f"{headline}\n\n"
                "Read the full report in the panel's Quality Reports tab "
                "(Business page)."
            ),
            related_task_id=task_id,
            requires_ack=ACK_REQUIRED_BY_TYPE[NotificationType.ALERT],
        )
        await self._persist_and_deliver(notification)
        from roboco.services.telegram_inbound import render_queue_item_text

        text = render_queue_item_text("sentinel", str(task_id)[:8], "", headline)
        if settings.panel_base_url:
            link = f"{settings.panel_base_url.rstrip('/')}/business?tab=quality-reports"
            text += f'\n<a href="{_esc_attr(link)}">Open in panel</a>'
        await self._send_telegram_deferred(
            text=text, reply_markup=None, disable_link_preview=True
        )

    async def notify_ceo_of_librarian_drafts(
        self, *, task: TaskTable, task_id: UUID, titles: list[str]
    ) -> None:
        """Best-effort CEO nudge the moment Librarian mines 1-3 playbook
        drafts.

        Mirrors ``notify_ceo_of_sentinel_report``/``_periscope_brief``
        exactly: display only — no approve/reject verb exists for THIS
        notification (the drafts themselves ride the EXISTING
        pending-playbook curation queue an Auditor's own
        ``approve_playbook``/``reject_playbook`` already reviews, never a new
        queue of their own) — "librarian" is a display-only ``_KIND_DISPLAY``
        entry, never added to ``_VALID_KINDS``/the approve-reject callback
        surface. A normal ALERT, not an APPROVAL — nothing here needs a CEO
        decision through a queue. The Auditor stays silent to agents
        throughout (spec §4's "Auditor boundary").
        """
        ceo = await self._get_ceo_agent()
        if not ceo:
            return
        from_agent = cast("UUID", task.assigned_to) if task.assigned_to else ceo.id
        title_list = ", ".join(titles)
        notification = NotificationTable(
            type=NotificationType.ALERT,
            priority=NotificationPriority.NORMAL,
            from_agent=from_agent,
            to_agents=[ceo.id],
            subject=f"Playbook drafts mined: {title_list[:100]}",
            body=(
                "The Auditor mined recurring patterns and drafted "
                f"{len(titles)} playbook(s): {title_list}\n\n"
                "Review them in the panel's pending-playbook curation queue "
                "(Overview page)."
            ),
            related_task_id=task_id,
            requires_ack=ACK_REQUIRED_BY_TYPE[NotificationType.ALERT],
        )
        await self._persist_and_deliver(notification)
        from roboco.services.telegram_inbound import render_queue_item_text

        text = render_queue_item_text("librarian", str(task_id)[:8], "", title_list)
        if settings.panel_base_url:
            link = f"{settings.panel_base_url.rstrip('/')}/overview"
            text += f'\n<a href="{_esc_attr(link)}">Open in panel</a>'
        await self._send_telegram_deferred(
            text=text, reply_markup=None, disable_link_preview=True
        )

    async def notify_ceo_of_budget_breach(
        self,
        *,
        task: TaskTable,
        task_id: UUID,
        cap_usd: float,
        spend_usd: float,
    ) -> None:
        """Create + deliver the CEO budget-breach notification.

        Shaped exactly like ``notify_ceo_of_escalation`` (APPROVAL/HIGH,
        actionable Telegram keyboard) — the CEO's options are the same shape
        (raise the cap, or leave it blocked). There is no human escalator
        here (the orchestrator's budget sweep triggers this), so ``from_agent``
        falls back to the task's own assignee, mirroring
        ``notify_ceo_of_completion``'s fallback.

        Names BOTH remediation steps: raising ``budget_usd`` alone does not
        resume the task — a PM must still call ``unblock`` (or the panel
        equivalent). ``unblock`` itself re-checks spend-vs-cap
        (``markers.BUDGET_BLOCKED``) and refuses while still over, so a raise
        that didn't actually clear the cap is caught there, not silently
        re-breached.
        """
        ceo = await self._get_ceo_agent()
        if not ceo:
            return
        from_agent = cast("UUID", task.assigned_to) if task.assigned_to else ceo.id
        notification = NotificationTable(
            type=NotificationType.APPROVAL,
            priority=NotificationPriority.HIGH,
            from_agent=from_agent,
            to_agents=[ceo.id],
            subject=f"Budget exceeded: {task.title or 'Unknown task'}",
            body=(
                f"Task {task_display(task, task_id)} was stopped and blocked: "
                f"its cost budget (${cap_usd:,.2f}) is exceeded — "
                f"${spend_usd:,.2f} spent so far.\n\n"
                "Two steps to resume it: 1) raise the task's Budget (USD) "
                "field (task detail, or the project's Monthly Budget if "
                "that's the cap), 2) have its PM call unblock — it stays "
                "blocked until unblock runs, and refuses again if the cap "
                "still isn't cleared. Or leave it blocked / cancel it."
            ),
            related_task_id=task_id,
            requires_ack=ACK_REQUIRED_BY_TYPE[NotificationType.APPROVAL],
        )
        await self._persist_and_deliver(notification)
        await self._notify_telegram(
            task_id=task_id, subject=notification.subject, actionable=True
        )

    async def notify_ceo_of_supersede_branch_cut_failure(
        self,
        *,
        task: TaskTable,
        task_id: UUID,
        branch: str,
        error: str,
    ) -> None:
        """CEO notification: the background branch cut exhausted retries.

        The umbrella is BLOCKED with HUMAN resolver after
        ``_MAX_BRANCH_CUT_ATTEMPTS`` failures. The CEO can unblock the task
        (the reconciliation sweep re-runs the cut) or cancel the umbrella.
        """
        ceo = await self._get_ceo_agent()
        if not ceo:
            return
        from_agent = cast("UUID", task.assigned_to) if task.assigned_to else ceo.id
        notification = NotificationTable(
            type=NotificationType.APPROVAL,
            priority=NotificationPriority.HIGH,
            from_agent=from_agent,
            to_agents=[ceo.id],
            subject=f"Supersede branch cut failed: {(task.title or 'Unknown')[:60]}",
            body=(
                f"Task {task_display(task, task_id)} was blocked: the background "
                f"branch cut for '{branch}' failed after multiple retries - "
                f"{error[:300]}.\n\n"
                "Unblock the task to re-run the branch cut (the reconciliation "
                "sweep picks it up automatically), or cancel the task if the "
                "git/forge credentials or repo are the problem."
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

    async def notify_ceo_of_postmortem(
        self,
        *,
        task: TaskTable,
        task_id: UUID,
        incident_summary: str,
        process_change_kind: str,
    ) -> None:
        """CEO-facing Coroner postmortem notification (spec §4).

        Shaped like ``notify_ceo_of_completion`` (INFO, non-actionable — no
        precedent for a dedicated report-notification type exists yet in this
        service): unlike ``notify_ceo_of_queue_item``, a postmortem has no
        per-item approve/reject decision to make, so this is display + a
        panel deep-link only, never an actionable Telegram keyboard, and
        never touches ``telegram_inbound``'s ``_VALID_KINDS`` approve/reject
        codec. Best-effort: a delivery failure must never block the
        postmortem it's reporting on.
        """
        ceo = await self._get_ceo_agent()
        if not ceo:
            return
        from_agent = cast("UUID", task.assigned_to) if task.assigned_to else ceo.id
        title = task.title or "Untitled task"
        notification = NotificationTable(
            type=NotificationType.ALERT,
            priority=NotificationPriority.NORMAL,
            from_agent=from_agent,
            to_agents=[ceo.id],
            subject=f"Postmortem: {title[:40]}",
            body=(
                f"The Auditor completed a Coroner postmortem.\n\n"
                f"{incident_summary}\n\n"
                f"Proposed process change: {process_change_kind}."
            ),
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

        Bypasses DB purpose-dedup: a second ``needs_revision`` on the same
        task from the same actor is a genuine repeat rework event that must
        still reach the auditor even while the first ALERT sits unacked —
        suppressing it would silently stop `_dispatch_audit_work` from
        re-spawning the auditor on later rework cycles.
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
        await self._persist_and_deliver(notification, bypass_purpose_dedup=True)

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

    async def _persist_and_deliver(
        self, notification: NotificationTable, *, bypass_purpose_dedup: bool = False
    ) -> bool:
        """Add to session, flush (to get an id), deliver. Caller commits.

        Returns True iff actually persisted+delivered, False if suppressed by
        either guard below. The Redis re-fire guard only ever applies to
        `_LOOP_PRONE_TYPES` (notification_dedup.py) — BLOCKER_ESCALATION,
        the type re-escalations use, is NOT one of them, so for that path
        this always returns True or raises; the real double-delivery guard
        for re-escalations is the CAS claim in
        `NotificationDeliveryService._claim_reescalation_slot`, upstream of
        this call. The DB purpose-dedup guard applies to ACK_REQUIRED_BY_TYPE
        action-required types (BLOCKER_ESCALATION included) — a retried
        `i_am_blocked`/escalate past the 60s Redis window still hits this.

        `bypass_purpose_dedup=True` skips ONLY the DB purpose-dedup check
        below (never the Redis re-fire guard) for a caller whose whole point
        is to intentionally re-send an identical (sender, type, task)
        signal — `_re_escalate_recipient` (the re-escalation ladder) and
        `notify_auditor_of_rework` (rework ALERTs) both pass this, since the
        prior copy being unacked is exactly why they fire again. This is
        scoped to the CALL PATH, not the notification type, so a first-send
        retried blocker (the gap this dedup was added to close) is still
        deduped normally.
        """
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
            return False
        # DB purpose-dedup: this path (task-handoff helpers) never went
        # through `NotificationService._create_notification`, so it never
        # got the same-purpose/unacked check that path applies. Without it,
        # a retried blocker/escalate past the Redis window above re-creates
        # a second unacked row for the same (sender, type, task, recipients).
        # Skipped entirely when the caller opted out (see docstring above).
        is_duplicate = (
            not bypass_purpose_dedup
            and notification.from_agent is not None
            and (
                await duplicate_unacked_notification_exists(
                    self.session,
                    from_agent=cast("UUID", notification.from_agent),
                    notification_type=notification.type,
                    related_task_id=cast("UUID | None", notification.related_task_id),
                    to_agents=cast("list[UUID]", notification.to_agents),
                )
            )
        )
        if is_duplicate:
            _log.info(
                "Suppressed duplicate notification (same purpose, unacked)",
                from_agent=str(notification.from_agent),
                type=notification.type.value if notification.type is not None else None,
                related_task_id=str(notification.related_task_id)
                if notification.related_task_id is not None
                else None,
            )
            return False
        self.session.add(notification)
        await self.session.flush()
        await self.deliver(require_uuid(notification.id))
        return True

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

        # Drop the per-recipient Redis dedup key so a post-ack re-send of the
        # same notification is not suppressed by a stale 60s window. The key
        # is per (type, sender, recipient, task, subject); only loop-prone
        # types carry one, and clear_dedup_key is a no-op fail-open for the
        # rest. Best-effort: a Redis miss never blocks the ack. Runs BEFORE
        # the flush so the row lock the flush takes is never held across a
        # Redis round-trip (a stalled Redis would starve concurrent writers
        # on this row into lock_timeout); clearing for an ack that then fails
        # is harmless — the key is only a re-send suppression window.
        await clear_dedup_key(
            ntype=notification.type,
            from_agent=cast("UUID", notification.from_agent),
            recipient=agent_id,
            related_task_id=cast("UUID | None", notification.related_task_id),
            subject=notification.subject,
        )

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
