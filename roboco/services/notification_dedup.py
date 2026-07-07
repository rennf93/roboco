"""Bounded re-fire guard for loop-prone notification types.

TASK_ASSIGNMENT / REVIEW_REQUEST / DOCUMENTATION_REQUEST / BROADCAST can be
re-fired by a PM every tick while a task sits in a state, flooding inboxes.
The existing DB dedup is gated to action-required types only and never fires
for these four, so a short Redis SET-NX window per (type, sender, recipient,
task) suppresses the re-fire here. Fail-open: Redis unavailable → never
suppress (a notification is never dropped because the dedup infra is down).
One-shot types (KNOWLEDGE_SHARE / MENTION / A2A_REQUEST) bypass entirely.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import redis.asyncio as redis

from roboco.config import settings
from roboco.models import NotificationType

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

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
