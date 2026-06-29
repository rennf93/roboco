"""NotificationService._create_notification purpose-based dedup (unit).

The dedup short-circuit returns before any row is created when a same-purpose
(same sender, type, task, overlapping recipients) notification is still
unacknowledged. The real-DB query shape is exercised by the route/integration
suites; here we assert the branch wiring with a mocked db context.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.models import NotificationPriority, NotificationType
from roboco.models.notification import CreateNotificationParams
from roboco.services.notification import NotificationService


class _FakeDBCtx:
    """Minimal async-context-manager yielding a mocked db handle."""

    def __init__(self, db: object) -> None:
        self._db = db

    async def __aenter__(self) -> object:
        return self._db

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _params() -> CreateNotificationParams:
    return CreateNotificationParams(
        notification_type=NotificationType.ALERT,
        priority=NotificationPriority.NORMAL,
        from_agent="from-1",
        to_agents=["to-1"],
        subject="s",
        body="b",
        related_task_id="t1",
    )


@pytest.mark.asyncio
async def test_create_notification_suppresses_same_purpose_duplicate() -> None:
    """An existing same-purpose unacked notification suppresses a new insert."""
    db = MagicMock()
    db.scalar = AsyncMock(return_value=uuid4())  # a same-purpose duplicate exists
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()

    svc = NotificationService()
    cc: Any = svc
    cc._resolve_recipients = AsyncMock(return_value=[uuid4()])
    with (
        patch(
            "roboco.services.notification.get_db_context",
            return_value=_FakeDBCtx(db),
        ),
        patch(
            "roboco.services.notification._resolve_agent_uuid",
            AsyncMock(return_value=uuid4()),
        ),
    ):
        await svc._create_notification(_params())

    # Dedup hit → no row created, nothing committed/delivered.
    db.add.assert_not_called()
    db.commit.assert_not_called()
    db.scalar.assert_awaited_once()


@pytest.mark.asyncio
async def test_informational_knowledge_share_not_deduped() -> None:
    """KNOWLEDGE_SHARE (informational, requires_ack=False) must NOT be deduped:
    a recipient who never acks the prior one would permanently suppress every
    subsequent knowledge-share from the same sender → silent learning-broadcast
    data loss. The dedup's anti-loop rationale only applies to action-required
    types."""
    db = MagicMock()
    # A same-purpose unacked KNOWLEDGE_SHARE prior exists — but it must NOT
    # suppress the new one.
    db.scalar = AsyncMock(return_value=uuid4())
    # ``db.add`` must give the row an id — the delivery path calls
    # ``require_uuid(notification.id)``.
    db.add = MagicMock(side_effect=lambda obj: setattr(obj, "id", uuid4()))
    db.flush = AsyncMock()
    db.commit = AsyncMock()

    svc = NotificationService()
    cc: Any = svc
    cc._resolve_recipients = AsyncMock(return_value=[uuid4()])
    params = CreateNotificationParams(
        notification_type=NotificationType.KNOWLEDGE_SHARE,
        priority=NotificationPriority.NORMAL,
        from_agent="from-1",
        to_agents=["to-1"],
        subject="New Learning: bug",
        body="a fresh learning the recipient has not seen",
        related_task_id=None,
    )
    with (
        patch(
            "roboco.services.notification.get_db_context",
            return_value=_FakeDBCtx(db),
        ),
        patch(
            "roboco.services.notification._resolve_agent_uuid",
            AsyncMock(return_value=uuid4()),
        ),
        patch(
            "roboco.services.notification_delivery.get_notification_delivery_service",
            lambda _db: MagicMock(deliver=AsyncMock(return_value=None)),
        ),
    ):
        await svc._create_notification(params)

    # Informational ⇒ NOT suppressed: a row was created + committed.
    db.add.assert_called_once()
    db.commit.assert_awaited_once()


# ---------------------------------------------------------------------------
# Bounded re-fire guard (loop-prone types)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_notification_suppresses_refire_when_guard_true() -> None:
    """A re-fire (guard True) short-circuits before the DB dedup query AND
    before any row is created/delivered — even though TASK_ASSIGNMENT is an
    action-required type the DB dedup never fires for."""
    db = MagicMock()
    db.scalar = AsyncMock()
    db.add = MagicMock()
    db.flush = AsyncMock()
    db.commit = AsyncMock()

    svc = NotificationService()
    cc: Any = svc
    cc._resolve_recipients = AsyncMock(return_value=[uuid4()])
    params = CreateNotificationParams(
        notification_type=NotificationType.TASK_ASSIGNMENT,
        priority=NotificationPriority.NORMAL,
        from_agent="from-1",
        to_agents=["to-1"],
        subject="s",
        body="b",
        related_task_id="t1",
    )
    with (
        patch(
            "roboco.services.notification.get_db_context",
            return_value=_FakeDBCtx(db),
        ),
        patch(
            "roboco.services.notification._resolve_agent_uuid",
            AsyncMock(return_value=uuid4()),
        ),
        patch(
            "roboco.services.notification.all_recipients_recently_notified",
            AsyncMock(return_value=True),
        ),
    ):
        await svc._create_notification(params)

    db.add.assert_not_called()
    db.commit.assert_not_called()
    db.scalar.assert_not_awaited()  # returned before the DB dedup query


@pytest.mark.asyncio
async def test_create_notification_passes_through_when_guard_false() -> None:
    """First fire (guard False) proceeds to row create + deliver."""
    db = MagicMock()
    db.add = MagicMock(side_effect=lambda obj: setattr(obj, "id", uuid4()))
    db.flush = AsyncMock()
    db.commit = AsyncMock()
    db.scalar = AsyncMock()  # TASK_ASSIGNMENT is_ack_required=False → not awaited

    svc = NotificationService()
    cc: Any = svc
    cc._resolve_recipients = AsyncMock(return_value=[uuid4()])
    params = CreateNotificationParams(
        notification_type=NotificationType.TASK_ASSIGNMENT,
        priority=NotificationPriority.NORMAL,
        from_agent="from-1",
        to_agents=["to-1"],
        subject="s",
        body="b",
        related_task_id="t1",
    )
    with (
        patch(
            "roboco.services.notification.get_db_context",
            return_value=_FakeDBCtx(db),
        ),
        patch(
            "roboco.services.notification._resolve_agent_uuid",
            AsyncMock(return_value=uuid4()),
        ),
        patch(
            "roboco.services.notification.all_recipients_recently_notified",
            AsyncMock(return_value=False),
        ),
        patch(
            "roboco.services.notification_delivery.get_notification_delivery_service",
            lambda _db: MagicMock(deliver=AsyncMock(return_value=None)),
        ),
    ):
        await svc._create_notification(params)

    db.add.assert_called_once()
    db.commit.assert_awaited_once()
