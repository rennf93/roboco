"""NotificationDeliveryService.sweep_expired_notifications re-escalation.

L26: an ack-required notification past its `expires_at` that is still
unacked must be re-escalated to the recipient's up-role (the PM's PM or
the CEO) BEFORE the sweep logs/expiring it — not just logged-and-dropped.
Combined with H12 (Task 6), an inattentive PM can't both miss a blocker
and prevent main-pm from seeing it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from roboco.models import NotificationPriority, NotificationType
from roboco.services.notification_delivery import NotificationDeliveryService


def _stale_notification(
    *,
    requires_ack: bool = True,
    acked: bool = False,
    recipient_id: UUID | None = None,
    from_agent_id: UUID | None = None,
) -> MagicMock:
    n = MagicMock()
    n.id = uuid4()
    n.type = NotificationType.BLOCKER_ESCALATION
    n.priority = NotificationPriority.HIGH
    n.subject = "Blocked: task X"
    n.body = "body"
    n.expires_at = datetime.now(UTC) - timedelta(minutes=5)
    n.timestamp = datetime.now(UTC) - timedelta(minutes=30)
    rid = recipient_id or uuid4()
    n.to_agents = [rid]
    n.acked_by = [rid] if acked else []
    n.read_by = []
    n.requires_ack = requires_ack
    n.from_agent = from_agent_id or uuid4()
    n.related_task_id = uuid4()
    return n


def _agent(slug: str, agent_id: UUID | None = None) -> MagicMock:
    a = MagicMock()
    a.id = agent_id or uuid4()
    a.slug = slug
    return a


def _svc_with_agents(
    session: MagicMock,
    *,
    recipient: MagicMock,
    escalation_target: MagicMock | None,
) -> Any:
    """Build a service with agent lookups stubbed.

    `_get_agent_by_id` resolves the unacked recipient; `_get_agent_by_slug`
    resolves the escalation target. `deliver` is stubbed so the re-escalation
    row flush + deliver path runs without a real DB.
    """
    svc = NotificationDeliveryService(session)
    cc: Any = svc
    cc._get_agent_by_id = AsyncMock(return_value=recipient)
    if escalation_target is not None:
        cc._get_agent_by_slug = AsyncMock(return_value=escalation_target)
    else:
        cc._get_agent_by_slug = AsyncMock(return_value=None)
    cc.deliver = AsyncMock(return_value=True)
    return svc


def _session_returning(notifications: list[MagicMock]) -> MagicMock:
    """A session whose `execute(...).scalars().all()` returns `notifications`."""
    session = MagicMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = notifications
    session.execute = AsyncMock(return_value=result)
    return session


@pytest.mark.asyncio
async def test_sweep_re_escalates_stale_unacked_ack_required() -> None:
    """Ack-required + past threshold + unacked → a re-escalation notification
    is persisted to the recipient's escalation target before the expiry log."""
    recipient = _agent("be-pm")
    target = _agent("main-pm")
    notif = _stale_notification(
        requires_ack=True, acked=False, recipient_id=recipient.id
    )

    session = _session_returning([notif])
    svc = _svc_with_agents(session, recipient=recipient, escalation_target=target)

    with (
        patch(
            "roboco.services.notification_delivery.all_recipients_recently_notified",
            AsyncMock(return_value=False),
        ),
        patch(
            "roboco.services.notification_delivery.get_escalation_target",
            return_value="main-pm",
        ),
    ):
        count = await svc.sweep_expired_notifications()

    assert count == 1
    # A re-escalation notification was added, addressed to the escalation target.
    added = [c for c in session.add.call_args_list if c.args]
    assert added, "expected a re-escalation row to be added to the session"
    re_escalated = added[0].args[0]
    assert re_escalated.to_agents == [target.id]
    assert re_escalated.type == NotificationType.BLOCKER_ESCALATION
    assert re_escalated.requires_ack is True
    assert "Re-escalation" in re_escalated.subject


@pytest.mark.asyncio
async def test_sweep_does_not_re_escalate_already_acked() -> None:
    """Ack-required + past threshold + fully acked → no re-escalation, count 0."""
    recipient = _agent("be-pm")
    target = _agent("main-pm")
    notif = _stale_notification(
        requires_ack=True, acked=True, recipient_id=recipient.id
    )

    session = _session_returning([notif])
    svc = _svc_with_agents(session, recipient=recipient, escalation_target=target)

    with (
        patch(
            "roboco.services.notification_delivery.all_recipients_recently_notified",
            AsyncMock(return_value=False),
        ),
        patch(
            "roboco.services.notification_delivery.get_escalation_target",
            return_value="main-pm",
        ),
    ):
        count = await svc.sweep_expired_notifications()

    assert count == 0
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_sweep_does_not_re_escalate_non_ack_required() -> None:
    """Non-ack-required + past threshold → no re-escalation (preserve existing
    behaviour: only ack-required-still-unacked rows re-escalate)."""
    recipient = _agent("be-dev-1")
    target = _agent("be-pm")
    notif = _stale_notification(
        requires_ack=False, acked=False, recipient_id=recipient.id
    )

    session = _session_returning([notif])
    svc = _svc_with_agents(session, recipient=recipient, escalation_target=target)

    with (
        patch(
            "roboco.services.notification_delivery.all_recipients_recently_notified",
            AsyncMock(return_value=False),
        ),
        patch(
            "roboco.services.notification_delivery.get_escalation_target",
            return_value="be-pm",
        ),
    ):
        count = await svc.sweep_expired_notifications()

    assert count == 0
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_sweep_skips_re_escalation_when_no_chain_target() -> None:
    """Recipient with no configured escalation target → no re-escalation, but
    the stale unacked count still surfaces (best-effort: missing chain is
    logged-and-skipped, never raises)."""
    recipient = _agent("ghost-role")
    notif = _stale_notification(
        requires_ack=True, acked=False, recipient_id=recipient.id
    )

    session = _session_returning([notif])
    svc = _svc_with_agents(session, recipient=recipient, escalation_target=None)

    with (
        patch(
            "roboco.services.notification_delivery.all_recipients_recently_notified",
            AsyncMock(return_value=False),
        ),
        patch(
            "roboco.services.notification_delivery.get_escalation_target",
            return_value=None,
        ),
    ):
        count = await svc.sweep_expired_notifications()

    assert count == 1  # still stale + unacked
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_sweep_dedup_suppresses_repeat_re_escalation() -> None:
    """A repeat sweep within the dedup window does not re-fire the same
    re-escalation (loop-prone guard in `_persist_and_deliver`)."""
    recipient = _agent("be-pm")
    target = _agent("main-pm")
    notif = _stale_notification(
        requires_ack=True, acked=False, recipient_id=recipient.id
    )

    session = _session_returning([notif])
    svc = _svc_with_agents(session, recipient=recipient, escalation_target=target)

    with (
        patch(
            "roboco.services.notification_delivery.all_recipients_recently_notified",
            AsyncMock(return_value=True),
        ),
        patch(
            "roboco.services.notification_delivery.get_escalation_target",
            return_value="main-pm",
        ),
    ):
        count = await svc.sweep_expired_notifications()

    assert count == 1  # stale + unacked still reported
    session.add.assert_not_called()  # dedup suppressed the re-escalation row
