"""Redis bus publish must be deferred until the DB commit lands so a rollback
drops the event (no phantom notification for a row that never became durable).

Integration tests against the migrated Postgres DB: the deferral uses
SQLAlchemy ``after_commit`` events and a recording bus stand-in.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast
from uuid import UUID, uuid4

import pytest
from roboco.db.tables import AgentTable, NotificationTable
from roboco.events import Event, EventType
from roboco.models import AgentRole, AgentStatus, NotificationPriority, NotificationType
from roboco.models.base import Team
from roboco.services.notification_delivery import get_notification_delivery_service

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class _RecordingBus:
    """Stand-in for StreamEventBus that records every published event.

    Mirrors the real bus surface used by ``deliver``: ``is_connected()``
    gates the publish path and ``publish`` is async. Recording lets the
    tests assert exactly when (and whether) the NOTIFICATION_SENT event
    fired — without a Redis stack.
    """

    def __init__(self) -> None:
        self.published: list[Event] = []

    def is_connected(self) -> bool:
        return True

    async def publish(self, event: Event) -> str:
        self.published.append(event)
        return "recorded"


def _drain_tasks(session: AsyncSession) -> list[asyncio.Task[object]]:
    """Pending deferred-publish drain tasks stashed on the session.

    The deferral helper stores the ``asyncio.create_task`` handles here so a
    test can await them deterministically instead of racing the event loop.
    """
    return list(session.info.get("_roboco_drain_tasks", []))


async def _await_drain(session: AsyncSession) -> None:
    """Wait for any scheduled deferred-publish tasks to finish."""
    tasks = _drain_tasks(session)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _seed_agents_and_notification(
    db: AsyncSession, *, recipients: int
) -> tuple[UUID, NotificationTable]:
    """Create a sender + N recipient agents and one flushed (uncommitted)
    notification addressed to them. Returns ``(notification_id, row)``.

    Flushed only — the row lives in the session's open transaction, matching
    the real pre-commit state ``deliver`` runs against.
    """
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

    recipient_ids: list[UUID] = []
    for i in range(recipients):
        r = AgentTable(
            id=uuid4(),
            name=f"Recipient {i}",
            slug=f"recipient-{i}-{uuid4().hex[:8]}",
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
        recipient_ids.append(cast("UUID", r.id))
    await db.flush()

    notification = NotificationTable(
        type=NotificationType.REVIEW_REQUEST,
        priority=NotificationPriority.NORMAL,
        from_agent=sender.id,
        to_agents=recipient_ids,
        subject="Please review",
        body="Body text",
        requires_ack=True,
    )
    db.add(notification)
    await db.flush()
    return cast("UUID", notification.id), notification


@pytest.mark.asyncio
async def test_deliver_does_not_publish_before_commit(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bus event must NOT fire until the session commits — ``deliver``
    only schedules; the event fires on commit."""
    bus = _RecordingBus()
    monkeypatch.setattr(
        "roboco.services.notification_delivery.get_event_bus", lambda: bus
    )

    notif_id, _ = await _seed_agents_and_notification(db_session, recipients=2)
    service = get_notification_delivery_service(db_session)
    await service.deliver(notif_id)

    # Pre-commit: nothing published yet (the row is not durable).
    assert bus.published == []


@pytest.mark.asyncio
async def test_deliver_publishes_after_commit(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Commit drains the deferred publish — one event per recipient."""
    bus = _RecordingBus()
    monkeypatch.setattr(
        "roboco.services.notification_delivery.get_event_bus", lambda: bus
    )

    recipient_count = 2
    notif_id, _ = await _seed_agents_and_notification(
        db_session, recipients=recipient_count
    )
    service = get_notification_delivery_service(db_session)
    await service.deliver(notif_id)
    assert bus.published == []  # still nothing before commit

    await db_session.commit()
    await _await_drain(db_session)

    assert len(bus.published) == recipient_count
    assert all(ev.type == EventType.NOTIFICATION_SENT for ev in bus.published)
    assert all(ev.data["notification_id"] == str(notif_id) for ev in bus.published)


@pytest.mark.asyncio
async def test_deliver_rollback_drops_phantom(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rollback instead of commit drops the pending publish — no phantom
    event for a row that never became durable."""
    bus = _RecordingBus()
    monkeypatch.setattr(
        "roboco.services.notification_delivery.get_event_bus", lambda: bus
    )

    notif_id, _ = await _seed_agents_and_notification(db_session, recipients=1)
    service = get_notification_delivery_service(db_session)
    await service.deliver(notif_id)

    await db_session.rollback()
    await _await_drain(db_session)

    assert bus.published == []


# --- #64: acknowledge must defer the ACK event (no phantom on rollback) ---


@pytest.mark.asyncio
async def test_acknowledge_does_not_publish_before_commit(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ACK bus event must NOT fire until the session commits — ``acknowledge``
    only schedules via the outbox; the event fires on commit, not at call time."""
    bus = _RecordingBus()
    monkeypatch.setattr(
        "roboco.services.notification_delivery.get_event_bus", lambda: bus
    )

    notif_id, notif = await _seed_agents_and_notification(db_session, recipients=1)
    recipient_id = cast("UUID", notif.to_agents[0])
    service = get_notification_delivery_service(db_session)
    await service.acknowledge(notif_id, recipient_id, ack_type="received")

    # Pre-commit: nothing published yet (the ack row state is not durable).
    assert bus.published == []


@pytest.mark.asyncio
async def test_acknowledge_publishes_after_commit(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Commit drains the deferred ACK publish — exactly one NOTIFICATION_ACKED."""
    bus = _RecordingBus()
    monkeypatch.setattr(
        "roboco.services.notification_delivery.get_event_bus", lambda: bus
    )

    notif_id, notif = await _seed_agents_and_notification(db_session, recipients=1)
    recipient_id = cast("UUID", notif.to_agents[0])
    service = get_notification_delivery_service(db_session)
    await service.acknowledge(notif_id, recipient_id, ack_type="received")
    assert bus.published == []  # still nothing before commit

    await db_session.commit()
    await _await_drain(db_session)

    assert len(bus.published) == 1
    assert bus.published[0].type == EventType.NOTIFICATION_ACKED
    assert bus.published[0].data["notification_id"] == str(notif_id)
    assert bus.published[0].data["agent_id"] == str(recipient_id)
    assert bus.published[0].data["ack_type"] == "received"


@pytest.mark.asyncio
async def test_acknowledge_rollback_drops_phantom(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rollback instead of commit drops the pending ACK publish — no phantom
    ACK event for an acknowledgement that never became durable."""
    bus = _RecordingBus()
    monkeypatch.setattr(
        "roboco.services.notification_delivery.get_event_bus", lambda: bus
    )

    notif_id, notif = await _seed_agents_and_notification(db_session, recipients=1)
    recipient_id = cast("UUID", notif.to_agents[0])
    service = get_notification_delivery_service(db_session)
    await service.acknowledge(notif_id, recipient_id, ack_type="received")

    await db_session.rollback()
    await _await_drain(db_session)

    assert bus.published == []
