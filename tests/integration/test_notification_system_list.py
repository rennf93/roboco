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
    """Newest ``limit`` fully-acked rows must NOT hide the oldest unacked one."""
    sender_id = await _seed_sender(db_session)
    recipient_id = await _seed_recipient(db_session)

    limit = 3
    base = datetime(2026, 1, 1, tzinfo=UTC)
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
