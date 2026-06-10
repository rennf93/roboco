"""NotificationService._create_notification purpose-based dedup (unit).

The dedup short-circuit returns before any row is created when a same-purpose
(same sender, type, task, overlapping recipients) notification is still
unacknowledged. The real-DB query shape is exercised by the route/integration
suites; here we assert the branch wiring with a mocked db context.
"""

from __future__ import annotations

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
    svc._resolve_recipients = AsyncMock(return_value=[uuid4()])  # type: ignore[method-assign]
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
