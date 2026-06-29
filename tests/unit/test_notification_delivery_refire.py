"""NotificationDeliveryService._persist_and_deliver re-fire guard.

Path 2 bypasses the DB dedup in NotificationService._create_notification, so
the same 60s Redis SET-NX guard gates it. Suppress (skip add/deliver) when the
guard says every recipient was just notified; pass through otherwise.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.models import NotificationPriority, NotificationType
from roboco.services.notification_delivery import NotificationDeliveryService


def _notification(
    ntype: NotificationType = NotificationType.TASK_ASSIGNMENT,
) -> MagicMock:
    n = MagicMock()
    n.id = uuid4()
    n.type = ntype
    n.from_agent = uuid4()
    n.to_agents = [uuid4(), uuid4()]
    n.related_task_id = uuid4()
    n.priority = NotificationPriority.NORMAL
    n.subject = "s"
    n.body = "b"
    n.requires_ack = True
    return n


def _svc(session: MagicMock) -> Any:
    """Build the service with ``deliver`` stubbed via an Any-typed alias so the
    reassignment stays type-clean (no method-assign suppression)."""
    svc = NotificationDeliveryService(session)
    cc: Any = svc
    cc.deliver = AsyncMock()
    return svc


@pytest.mark.asyncio
async def test_persist_and_deliver_suppresses_when_guard_true() -> None:
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    svc = _svc(session)

    with patch(
        "roboco.services.notification_delivery.all_recipients_recently_notified",
        AsyncMock(return_value=True),
    ):
        await svc._persist_and_deliver(_notification())

    session.add.assert_not_called()
    session.flush.assert_not_awaited()
    svc.deliver.assert_not_awaited()


@pytest.mark.asyncio
async def test_persist_and_deliver_passes_through_when_guard_false() -> None:
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    svc = _svc(session)

    notif = _notification()
    with patch(
        "roboco.services.notification_delivery.all_recipients_recently_notified",
        AsyncMock(return_value=False),
    ):
        await svc._persist_and_deliver(notif)

    session.add.assert_called_once_with(notif)
    session.flush.assert_awaited_once()
    svc.deliver.assert_awaited_once()
