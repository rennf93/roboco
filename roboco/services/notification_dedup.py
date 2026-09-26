"""Notification dedup helpers: a Redis re-fire guard plus a shared DB
purpose-dedup query.

TASK_ASSIGNMENT / REVIEW_REQUEST / DOCUMENTATION_REQUEST / BROADCAST can be
re-fired by a PM every tick while a task sits in a state, flooding inboxes.
The existing DB dedup is gated to action-required types only and never fires
for these four, so a short Redis SET-NX window per (type, sender, recipient,
task) suppresses the re-fire here. Fail-open: Redis unavailable → never
suppress (a notification is never dropped because the dedup infra is down).
One-shot types (KNOWLEDGE_SHARE / MENTION / A2A_REQUEST) bypass entirely.

``duplicate_unacked_notification_exists`` is the DB-side purpose-dedup query
(same sender + type + task + exact recipient set, prior still unacked),
shared by ``NotificationService._create_notification`` and
``NotificationDeliveryService._persist_and_deliver`` so both paths apply the
same semantics instead of each growing its own query.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import redis.asyncio as redis
from sqlalchemy import select

from roboco.config import settings
from roboco.foundation.policy.communications import ACK_REQUIRED_BY_TYPE
from roboco.models import NotificationType

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    from sqlalchemy import Select
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Loop-prone: a coordinator re-fires these every tick while the task sits in a
# state. One-shot types (knowledge share, mention, a2a request) are excluded.
_LOOP_PRONE_TYPES = frozenset(
    {
        NotificationType.TASK_ASSIGNMENT,
        NotificationType.REVIEW_REQUEST,
        NotificationType.DOCUMENTATION_REQUEST,
        NotificationType.BROADCAST,
    }
)

# 60s: long enough to coalesce a re-fire storm, short enough that a genuine
# follow-up (state actually changed, a new ack window) still lands.
_DEDUP_TTL_SECONDS = 60

# The unacked-candidate query is bounded: the newest candidates are the
# relevant ones for dedup, and the B12 screen must never turn a notify
# storm into N classifier calls on the create path.
_CANDIDATE_ROWS_CAP = 8


def _key(
    ntype: NotificationType,
    from_agent: UUID | str,
    recipient: UUID | str,
    related_task_id: UUID | str | None,
    subject: str,
) -> str:
    task_part = str(related_task_id) if related_task_id is not None else "none"
    # subject is the purpose discriminator: "Task unblocked" and "Task ready
    # for review" are both TASK_ASSIGNMENT but carry distinct intent, so both
    # must survive the 60s window. subject is stable per sender+state (callers
    # use fixed templates), so a true re-fire still dedups.
    prefix = f"roboco:notif_dedup:{ntype.value}:{from_agent}:{recipient}"
    return f"{prefix}:{task_part}:{subject}"


async def all_recipients_recently_notified(
    *,
    ntype: NotificationType,
    from_agent: UUID | str | None,
    recipients: Sequence[UUID | str],
    related_task_id: UUID | str | None,
    subject: str,
) -> bool:
    """True iff every recipient already holds the dedup key (a re-fire).

    Per-recipient SET-NX: acquires (marks) keys for recipients NOT yet
    notified this window, so the next fire converges toward full suppression.
    Suppresses only when NO recipient was fresh (all already held). Fail-open:
    a Redis error → False (never drop a notification over dedup infra).
    """
    if ntype not in _LOOP_PRONE_TYPES:
        return False
    if from_agent is None or not recipients:
        return False

    try:
        conn = redis.from_url(settings.redis_url)
        try:
            any_fresh = False
            for recipient in recipients:
                acquired = await conn.set(
                    _key(ntype, from_agent, recipient, related_task_id, subject),
                    "1",
                    nx=True,
                    ex=_DEDUP_TTL_SECONDS,
                )
                if acquired:
                    any_fresh = True
            return not any_fresh
        finally:
            await conn.aclose()
    except Exception as exc:
        logger.warning("notification dedup probe failed (redis): %s", exc)
        return False


async def clear_dedup_key(
    *,
    ntype: NotificationType,
    from_agent: UUID | str,
    recipient: UUID | str,
    related_task_id: UUID | str | None,
    subject: str,
) -> None:
    """DEL the per-recipient dedup key after an ack so a post-ack re-send
    is not suppressed by a stale 60s window. Best-effort (fail-open)."""
    try:
        conn = redis.from_url(settings.redis_url)
        try:
            await conn.delete(
                _key(ntype, from_agent, recipient, related_task_id, subject)
            )
        finally:
            await conn.aclose()
    except Exception as exc:
        logger.warning("notification dedup clear failed (redis): %s", exc)


def _unacked_overlap_query(
    *,
    from_agent: UUID,
    notification_type: NotificationType,
    related_task_id: UUID | str | None,
    to_agents_list: list[UUID],
) -> Select[tuple[Any, Any, Any, Any]]:
    """The unacked same-purpose candidate query: same sender, same type,
    overlapping recipient set (not yet fully acked), same task (or both
    taskless). Bounded: the newest candidates are the relevant ones for
    dedup, and the B12 screen must never turn a notify storm into N
    classifier calls on the create path. Local NotificationTable import:
    avoid import cycle."""
    from roboco.db.tables import NotificationTable

    return (
        select(
            NotificationTable.id,
            NotificationTable.to_agents,
            NotificationTable.subject,
            NotificationTable.body,
        )
        .where(NotificationTable.from_agent == from_agent)
        .where(NotificationTable.type == notification_type)
        .where(NotificationTable.to_agents.overlap(to_agents_list))
        .where(~NotificationTable.acked_by.contains(to_agents_list))
        .where(
            NotificationTable.related_task_id == related_task_id
            if related_task_id is not None
            else NotificationTable.related_task_id.is_(None)
        )
        .order_by(NotificationTable.created_at.desc())
        .limit(_CANDIDATE_ROWS_CAP)
    )


async def duplicate_unacked_notification_exists(  # noqa: PLR0913
    db: AsyncSession,
    *,
    from_agent: UUID,
    notification_type: NotificationType,
    related_task_id: UUID | str | None,
    to_agents: Sequence[UUID],
    subject: str | None = None,
    body: str | None = None,
) -> bool:
    """True when an unacked same-purpose notification already exists.

    Purpose-based dedup (CEO directive, 2026-06-10): same sender, type,
    task, EQUAL recipient set, while a prior one is still unacked — agents
    re-send the same signal (often reworded) and each copy inflates the
    recipient's unacked set, soft-blocking i_am_idle and driving respawn
    churn. Body text is NOT compared. Dedup applies only to ACTION-REQUIRED
    types; informational carries distinct content per send and acking is
    voluntary, so deduping them would silently drop broadcasts.

    Recipient set must be EXACTLY equal — overlapping-but-not-equal sets do
    NOT suppress. A blocker sent to {be-pm, main-pm} after an unacked one to
    {be-pm} alone must reach main-pm (the prior's recipients are a strict
    subset). Overlap is the SQL filter; the exact-set-equality check runs in
    Python against the fetched candidate rows.

    B12 semantic screen (optional, default off): callers that pass
    ``subject`` and ``body`` opt in to one Decisions screen against
    EQUAL-set unacked priors only, and it can only ADD delivery, never
    subtract it: when the pilot is ON and confidently says the new
    notification carries NEW information (not a reworded duplicate), the
    deterministic suppression is lifted; off/shadow/below-floor/any error
    suppresses exactly as today. Overlapping-but-not-equal sets are never
    screened: their unequal recipients must not lose the notification, so
    a body-aware verdict there could only ever do harm.

    Primitive-typed (db/from_agent/notification_type/related_task_id/
    to_agents) so both ``NotificationService._create_notification`` (which
    holds a ``CreateNotificationParams``) and
    ``NotificationDeliveryService._persist_and_deliver`` (which only holds a
    built ``NotificationTable``, no params object) can share the one query
    instead of each growing a divergent copy. The extra ``subject``/``body``
    opt-in kwargs keep this the one shared query for both existing callers
    (positional-contract kwargs like the rest, hence the targeted PLR0913
    noqa rather than bundling the probe into an object no caller asked for).
    """
    if not ACK_REQUIRED_BY_TYPE.get(notification_type, True):
        return False
    to_agents_list = list(to_agents)
    new_set = set(to_agents_list)
    dup_q = _unacked_overlap_query(
        from_agent=from_agent,
        notification_type=notification_type,
        related_task_id=related_task_id,
        to_agents_list=to_agents_list,
    )
    result = await db.execute(dup_q)
    rows = result.all()
    equal_rows = [row for row in rows if set(row[1]) == new_set]
    if equal_rows:
        distinct_ok = (
            subject is not None
            and body is not None
            and await _semantic_distinct_delivery(
                db,
                rows=equal_rows,
                new_set=new_set,
                content=(subject, body),
            )
        )
        if distinct_ok:
            logger.info(
                "Delivered notification despite unacked same-purpose prior "
                "(B12: confidently distinct): from_agent=%s type=%s "
                "related_task_id=%s to_agents=%s",
                from_agent,
                notification_type.value,
                related_task_id,
                to_agents_list,
            )
            return False
        logger.info(
            "Suppressed duplicate notification (same purpose, unacked): "
            "from_agent=%s type=%s related_task_id=%s to_agents=%s",
            from_agent,
            notification_type.value,
            related_task_id,
            to_agents_list,
        )
        return True
    return False


async def _probe_semantic_distinct(
    db: AsyncSession,
    *,
    subject: str,
    body: str,
    row: Any,
    new_set: set[UUID],
) -> bool:
    """One B12 distinct-delivery screen against a single EQUAL-set
    candidate row: True only when the pilot is ON and confidently says
    the new notification carries new information (not a reworded
    duplicate). Fail-closed to suppression: any decisions failure returns
    False, which keeps today's deterministic suppression."""
    from roboco.services.decisions.pilots_infra import semantic_distinct_delivery

    prior_recipients = set(row[1])
    try:
        return await semantic_distinct_delivery(
            db,
            new_subject=subject,
            new_body=body,
            prior_subject=str(row[2] or ""),
            prior_body=str(row[3] or ""),
            recipients=[str(r) for r in new_set],
            prior_recipients=[str(r) for r in prior_recipients],
        )
    except Exception as exc:
        logger.warning("notification semantic-dedup probe failed (suppress): %s", exc)
        return False


async def _semantic_distinct_delivery(
    db: AsyncSession,
    *,
    rows: Sequence[Any],
    new_set: set[UUID],
    content: tuple[str, str],
) -> bool:
    """True when EVERY equal-set unacked prior is confidently NOT a
    semantic duplicate of the new notification, so the deterministic
    suppression may be lifted (additive delivery, the only doctrine-safe
    direction for a body-aware screen beside the exact-set rule). Any
    unsure/failing probe keeps the suppression. Bounded by the query's
    candidate cap."""
    subject, body = content
    for row in rows:
        if not await _probe_semantic_distinct(
            db,
            subject=subject,
            body=body,
            row=row,
            new_set=new_set,
        ):
            return False
    return True
