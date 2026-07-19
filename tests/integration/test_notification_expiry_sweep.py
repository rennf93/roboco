"""expires_at is now stamped at creation (NotificationService) and actually
matched by NotificationDeliveryService.sweep_expired_notifications' SQL
WHERE clause — before the fix the column was never written, so this query
always matched zero rows regardless of how stale a notification was.

Integration tests against the migrated Postgres DB: `sweep_expired_notifications`
issues a real `expires_at < now()` query, so a mocked session (as
`tests/unit/services/test_notification_delivery.py` uses) can't exercise it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

import pytest
from roboco.config import settings
from roboco.db.tables import AgentTable, NotificationTable
from roboco.models import AgentRole, AgentStatus, NotificationPriority, NotificationType
from roboco.models.base import Team
from roboco.models.notification import CreateNotificationParams
from roboco.services.notification import NotificationService
from roboco.services.notification_delivery import get_notification_delivery_service
from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def _seed_agent(db: AsyncSession, *, role: AgentRole, slug: str) -> UUID:
    agent = AgentTable(
        id=uuid4(),
        name=slug,
        slug=slug,
        role=role,
        team=Team.BACKEND,
        status=AgentStatus.ACTIVE,
        model_config={},
        system_prompt=slug,
        capabilities=[],
        permissions={},
        metrics={},
    )
    db.add(agent)
    await db.flush()
    return cast("UUID", agent.id)


@pytest.mark.asyncio
async def test_created_notification_expires_at_is_stamped_and_matched_by_sweep(
    db_session: AsyncSession,
) -> None:
    """End-to-end: NotificationService._create_notification stamps expires_at
    for an ack-required row, and once that deadline is in the past,
    sweep_expired_notifications' real Postgres query finds it (count 1) —
    the exact round trip that was a dead no-op before this fix, since
    expires_at was always NULL and `expires_at < now()` never matched."""
    unique = uuid4().hex[:8]
    sender = await _seed_agent(
        db_session, role=AgentRole.DEVELOPER, slug=f"sndr-{unique}"
    )
    recipient = await _seed_agent(
        db_session, role=AgentRole.CELL_PM, slug=f"pm-{unique}"
    )

    svc = NotificationService()
    await svc._create_notification(
        CreateNotificationParams(
            notification_type=NotificationType.BLOCKER_ESCALATION,
            priority=NotificationPriority.HIGH,
            from_agent=str(sender),
            to_agents=[str(recipient)],
            subject="blocked",
            body="external dependency",
        ),
        db_session=db_session,
    )
    await db_session.flush()

    row = (
        await db_session.execute(
            select(NotificationTable).where(
                NotificationTable.type == NotificationType.BLOCKER_ESCALATION,
                NotificationTable.from_agent == sender,
            )
        )
    ).scalar_one()
    assert row.expires_at is not None
    assert row.requires_ack is True

    # Backdate it past the deadline (no real clock wait) and confirm the
    # sweep's `expires_at < now()` predicate now actually matches.
    row.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.flush()

    deliv = get_notification_delivery_service(db_session)
    count = await deliv.sweep_expired_notifications()
    assert count >= 1


@pytest.mark.asyncio
async def test_directly_stamped_expired_row_is_matched_by_sweep_query(
    db_session: AsyncSession,
) -> None:
    """Isolates the sweep query mechanics from creation: a hand-built
    ack-required, unacked row with expires_at in the past must be counted."""
    unique = uuid4().hex[:8]
    sender = await _seed_agent(
        db_session, role=AgentRole.DEVELOPER, slug=f"s2-{unique}"
    )
    recipient = await _seed_agent(db_session, role=AgentRole.QA, slug=f"r2-{unique}")

    notification = NotificationTable(
        type=NotificationType.ALERT,
        priority=NotificationPriority.HIGH,
        from_agent=sender,
        to_agents=[recipient],
        subject="stale alert",
        body="body",
        requires_ack=True,
        expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    db_session.add(notification)
    await db_session.flush()

    deliv = get_notification_delivery_service(db_session)
    count = await deliv.sweep_expired_notifications()
    assert count >= 1


@pytest.mark.asyncio
async def test_zero_ttl_disables_expires_at_stamping_end_to_end(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """notification_ack_ttl_hours=0 leaves expires_at NULL even for an
    ack-required notification created through the real service."""
    monkeypatch.setattr(settings, "notification_ack_ttl_hours", 0)
    unique = uuid4().hex[:8]
    sender = await _seed_agent(
        db_session, role=AgentRole.DEVELOPER, slug=f"s3-{unique}"
    )
    recipient = await _seed_agent(
        db_session, role=AgentRole.CELL_PM, slug=f"pm3-{unique}"
    )

    svc = NotificationService()
    await svc._create_notification(
        CreateNotificationParams(
            notification_type=NotificationType.BLOCKER_ESCALATION,
            priority=NotificationPriority.HIGH,
            from_agent=str(sender),
            to_agents=[str(recipient)],
            subject="blocked",
            body="external dependency",
        ),
        db_session=db_session,
    )
    await db_session.flush()

    row = (
        await db_session.execute(
            select(NotificationTable).where(
                NotificationTable.type == NotificationType.BLOCKER_ESCALATION,
                NotificationTable.from_agent == sender,
            )
        )
    ).scalar_one()
    assert row.expires_at is None
