"""list_system_notifications must not let a SQL limit mask older unacked rows.

The pending_ack_only "not fully acked" predicate is not SQL-expressible against
the array columns, so it is applied post-fetch. Applying the SQL ``limit``
before that Python filter lets a window of newer fully-acked rows fill the
limit and hide older unacked notifications the operator still needs to act on.
These integration tests pin the over-fetch-then-slice behaviour against the
real Postgres schema.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

import pytest
from roboco.db.tables import AgentTable, NotificationTable
from roboco.models import AgentRole, AgentStatus, NotificationPriority, NotificationType
from roboco.models.base import Team
from roboco.services.notification_delivery import get_notification_delivery_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_sender(db: AsyncSession) -> UUID:
    sender = AgentTable(
        id=uuid4(),
        name="Sender",
        slug=f"sender-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="sender",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db.add(sender)
    await db.flush()
    return cast("UUID", sender.id)


async def _seed_recipient(db: AsyncSession) -> UUID:
    r = AgentTable(
        id=uuid4(),
        name="Recipient",
        slug=f"recipient-{uuid4().hex[:8]}",
        role=AgentRole.QA,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="recipient",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db.add(r)
    await db.flush()
    return cast("UUID", r.id)


async def _add_notification(
    db: AsyncSession,
    *,
    sender_id: UUID,
    recipient_id: UUID,
    timestamp: datetime,
    fully_acked: bool,
) -> UUID:
    n = NotificationTable(
        type=NotificationType.REVIEW_REQUEST,
        priority=NotificationPriority.NORMAL,
        from_agent=sender_id,
        to_agents=[recipient_id],
        subject="Please review",
        body="Body text",
        requires_ack=True,
        acked_by=[recipient_id] if fully_acked else [],
        timestamp=timestamp,
    )
    db.add(n)
    await db.flush()
    return cast("UUID", n.id)


@pytest.mark.asyncio
async def test_pending_ack_only_not_masked_by_fully_acked_window(
    db_session: AsyncSession,
) -> None:
    """Newest ``limit`` fully-acked rows must NOT hide the oldest unacked one.

    ``list_system_notifications(pending_ack_only=True)`` reads the WHOLE
    requires_ack set with no recipient scope (there is none to scope by,
    it's the system-wide view), so a fixed calendar date collides with any
    other test's real-"now"-timestamped committed row in the shared suite
    DB (e.g. a still-unacked CEO queue-item notification another test left
    behind). Anchoring on ``now() + 10 years`` instead keeps this test's own
    4 rows the newest in the whole table regardless of what else is
    committed, so the assertions only ever depend on ordering WITHIN that
    set, not on the table being otherwise empty.
    """
    sender_id = await _seed_sender(db_session)
    recipient_id = await _seed_recipient(db_session)

    limit = 3
    base = datetime.now(UTC) + timedelta(days=3650)
    # Newest `limit` rows (largest timestamps) are fully acked; the oldest is not.
    acked_ids = [
        await _add_notification(
            db_session,
            sender_id=sender_id,
            recipient_id=recipient_id,
            timestamp=base + timedelta(minutes=i + 1),
            fully_acked=True,
        )
        for i in range(limit)
    ]
    unacked_id = await _add_notification(
        db_session,
        sender_id=sender_id,
        recipient_id=recipient_id,
        timestamp=base,
        fully_acked=False,
    )
    _ = acked_ids  # only the unacked one should survive the filter

    service = get_notification_delivery_service(db_session)
    result = await service.list_system_notifications(
        pending_ack_only=True, type_filter=None, limit=limit
    )

    result_ids = {n.id for n in result}
    assert unacked_id in result_ids
    # The fully-acked rows that filled the limit window are filtered out.
    assert all(aid not in result_ids for aid in acked_ids)


@pytest.mark.asyncio
async def test_pending_ack_only_slices_to_limit(db_session: AsyncSession) -> None:
    """More unacked rows than ``limit`` → exactly ``limit`` returned, newest first."""
    sender_id = await _seed_sender(db_session)
    recipient_id = await _seed_recipient(db_session)

    limit = 2
    base = datetime(2026, 2, 1, tzinfo=UTC)
    for i in range(limit + 2):
        await _add_notification(
            db_session,
            sender_id=sender_id,
            recipient_id=recipient_id,
            timestamp=base + timedelta(minutes=i),
            fully_acked=False,
        )

    service = get_notification_delivery_service(db_session)
    result = await service.list_system_notifications(
        pending_ack_only=True, type_filter=None, limit=limit
    )
    assert len(result) == limit


@pytest.mark.asyncio
async def test_get_notification_count_matches_in_memory_computation_at_scale(
    db_session: AsyncSession,
) -> None:
    """SQL COUNT rewrite must match the old in-memory sum() for a large, mixed seed.

    Seeds >1000 rows for one agent, mixing read/unread, acked/unacked, and
    requires_ack true/false so all three counters exercise non-trivial values,
    then asserts the service's result equals what the OLD Python-side
    computation (``len(rows)`` / ``sum(agent_id not in read_by)`` /
    ``sum(requires_ack and agent_id not in acked_by)``) would produce.
    """
    sender_id = await _seed_sender(db_session)
    recipient_id = await _seed_recipient(db_session)
    other_id = uuid4()  # noise recipient — must not affect recipient_id's counts

    base = datetime(2026, 4, 1, tzinfo=UTC)
    rows: list[NotificationTable] = []
    seed_size = 1050
    for i in range(seed_size):
        read = i % 2 == 0
        acked = i % 3 == 0
        requires_ack = i % 5 != 0
        n = NotificationTable(
            type=NotificationType.REVIEW_REQUEST,
            priority=NotificationPriority.NORMAL,
            from_agent=sender_id,
            to_agents=[recipient_id, other_id] if i % 7 == 0 else [recipient_id],
            subject=f"Notification {i}",
            body="Body text",
            requires_ack=requires_ack,
            acked_by=[recipient_id] if acked else [],
            read_by=[recipient_id] if read else [],
            timestamp=base + timedelta(seconds=i),
        )
        rows.append(n)
    db_session.add_all(rows)
    await db_session.flush()

    expected_total = len(rows)
    expected_unread = sum(1 for n in rows if recipient_id not in n.read_by)
    expected_pending_ack = sum(
        1 for n in rows if n.requires_ack and recipient_id not in n.acked_by
    )
    assert expected_unread not in (0, expected_total)
    assert expected_pending_ack not in (0, expected_total)

    service = get_notification_delivery_service(db_session)
    counts = await service.get_notification_count(recipient_id)

    assert counts == {
        "total": expected_total,
        "unread": expected_unread,
        "pending_ack": expected_pending_ack,
    }


@pytest.mark.asyncio
async def test_non_pending_branch_keeps_sql_limit(db_session: AsyncSession) -> None:
    """Without pending_ack_only the SQL limit still bounds the result."""
    sender_id = await _seed_sender(db_session)
    recipient_id = await _seed_recipient(db_session)

    limit = 2
    base = datetime(2026, 3, 1, tzinfo=UTC)
    for i in range(limit + 3):
        await _add_notification(
            db_session,
            sender_id=sender_id,
            recipient_id=recipient_id,
            timestamp=base + timedelta(minutes=i),
            fully_acked=False,
        )

    service = get_notification_delivery_service(db_session)
    result = await service.list_system_notifications(
        pending_ack_only=False, type_filter=None, limit=limit
    )
    assert len(result) == limit
