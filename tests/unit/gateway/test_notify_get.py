"""Tests for ContentActions.notify_get — read-one-notification (marks read).

`notify_get` used to swallow ANY exception from
`get_for_recipient_and_mark_read` into `Envelope.not_found` — including a DB
error from the mark-read UPDATE (e.g. hitting `lock_timeout`), which poisoned
the session for the rest of the transaction and surfaced later as an opaque
`PendingRollbackError`, while also lying to the calling agent that an
existing notification didn't exist. The fix narrows the catch to the two real
domain outcomes (`NotFoundError`, `PermissionError`); anything else must
propagate so the session actually rolls back.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.base import NotFoundError
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps
from sqlalchemy.exc import OperationalError


def _make_deps(**overrides: AsyncMock) -> ContentActionsDeps:
    task = overrides.get("task", AsyncMock())
    git = overrides.get("git", AsyncMock())
    a2a = overrides.get("a2a", AsyncMock())
    journal = overrides.get("journal", AsyncMock())
    workspace = overrides.get("workspace", AsyncMock())
    notifications = overrides.get("notifications", AsyncMock())
    notification_delivery = overrides.get("notification_delivery", AsyncMock())
    return ContentActionsDeps(
        task=task,
        git=git,
        a2a=a2a,
        journal=journal,
        workspace=workspace,
        notifications=notifications,
        notification_delivery=notification_delivery,
    )


@pytest.mark.asyncio
async def test_notify_get_not_found_error_maps_to_not_found() -> None:
    """A genuinely missing notification -> Envelope.not_found."""
    notification_id = uuid4()
    notif_delivery = AsyncMock()
    notif_delivery.get_for_recipient_and_mark_read.side_effect = NotFoundError(
        resource_type="Notification", resource_id=str(notification_id)
    )
    deps = _make_deps(notification_delivery=notif_delivery)
    ca = ContentActions(deps)

    env = await ca.notify_get(agent_id=uuid4(), notification_id=notification_id)
    body = env.as_dict()

    assert body["error"] == "not_found"
    assert str(notification_id) in body["message"]


@pytest.mark.asyncio
async def test_notify_get_permission_error_maps_to_not_found() -> None:
    """A recipient mismatch -> Envelope.not_found (never leaks a 403/details)."""
    notification_id = uuid4()
    notif_delivery = AsyncMock()
    notif_delivery.get_for_recipient_and_mark_read.side_effect = PermissionError(
        "view notification: not a recipient"
    )
    deps = _make_deps(notification_delivery=notif_delivery)
    ca = ContentActions(deps)

    env = await ca.notify_get(agent_id=uuid4(), notification_id=notification_id)
    body = env.as_dict()

    assert body["error"] == "not_found"
    assert str(notification_id) in body["message"]


@pytest.mark.asyncio
async def test_notify_get_db_error_propagates() -> None:
    """A DB-shaped failure (e.g. the mark-read UPDATE hitting lock_timeout)
    must NOT be swallowed into not_found — it has to propagate so the
    session actually rolls back instead of silently poisoning the
    transaction for the caller's later commit."""
    notification_id = uuid4()
    notif_delivery = AsyncMock()
    notif_delivery.get_for_recipient_and_mark_read.side_effect = OperationalError(
        "UPDATE notifications ...", {}, Exception("lock timeout")
    )
    deps = _make_deps(notification_delivery=notif_delivery)
    ca = ContentActions(deps)

    with pytest.raises(OperationalError):
        await ca.notify_get(agent_id=uuid4(), notification_id=notification_id)


@pytest.mark.asyncio
async def test_notify_get_success_returns_notification_and_marks_read() -> None:
    """Happy path: the resolved notification's fields land in evidence."""
    notification_id = uuid4()
    from_agent = uuid4()
    n = MagicMock()
    n.id = notification_id
    n.type = "alert"
    n.priority = "normal"
    n.subject = "subject line"
    n.body = "body text"
    n.requires_ack = False
    n.from_agent = from_agent

    notif_delivery = AsyncMock()
    notif_delivery.get_for_recipient_and_mark_read.return_value = n
    deps = _make_deps(notification_delivery=notif_delivery)
    ca = ContentActions(deps)

    env = await ca.notify_get(agent_id=uuid4(), notification_id=notification_id)
    body = env.as_dict()

    assert body["error"] is None
    assert body["evidence"]["id"] == str(notification_id)
    assert body["evidence"]["subject"] == "subject line"
    assert body["evidence"]["from_agent"] == str(from_agent)
    notif_delivery.get_for_recipient_and_mark_read.assert_awaited_once()
