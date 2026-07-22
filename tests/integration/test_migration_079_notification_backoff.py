"""Re-escalation backoff columns on notifications (migration 079).

Migration 079 adds ``notifications.reescalation_count`` /
``.reescalation_delivered_count`` (integer, not null, default 0) and
``.last_reescalated_at`` (timestamptz, null). The real upgrade/downgrade
chain is verified separately against a throwaway Postgres; these assertions
guard the resulting schema shape and a value round-trip.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from roboco.db.tables import AgentTable, NotificationTable
from roboco.models import AgentRole, AgentStatus, NotificationPriority, NotificationType
from roboco.models.base import Team
from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_notification(db_session: AsyncSession) -> NotificationTable:
    sender = AgentTable(
        id=uuid4(),
        name="Dev",
        slug=f"be-dev-{uuid4().hex[:8]}",
        role=AgentRole.DEVELOPER,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt="dev",
        capabilities=[],
        permissions={},
        metrics={},
    )
    db_session.add(sender)
    await db_session.flush()
    notification = NotificationTable(
        type=NotificationType.BLOCKER_ESCALATION,
        priority=NotificationPriority.HIGH,
        from_agent=sender.id,
        to_agents=[sender.id],
        subject="stale",
        body="body",
        requires_ack=True,
    )
    db_session.add(notification)
    await db_session.flush()
    return notification


@pytest.mark.asyncio
async def test_reescalation_backoff_columns_default(db_session: AsyncSession) -> None:
    notification = await _seed_notification(db_session)
    assert notification.reescalation_count == 0
    assert notification.reescalation_delivered_count == 0
    assert notification.last_reescalated_at is None


@pytest.mark.asyncio
async def test_reescalation_backoff_columns_round_trip(
    db_session: AsyncSession,
) -> None:
    notification = await _seed_notification(db_session)
    stamped_at = datetime.now(UTC)
    attempts, delivered = 3, 2
    notification.reescalation_count = attempts
    notification.reescalation_delivered_count = delivered
    notification.last_reescalated_at = stamped_at
    await db_session.flush()

    row = (
        await db_session.execute(
            select(NotificationTable).where(NotificationTable.id == notification.id)
        )
    ).scalar_one()
    assert row.reescalation_count == attempts
    assert row.reescalation_delivered_count == delivered
    assert row.last_reescalated_at == stamped_at
