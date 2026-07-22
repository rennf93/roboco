"""NotificationDeliveryService.sweep_expired_notifications re-escalation.

L26: an ack-required notification past its `expires_at` that is still
unacked must be re-escalated to the recipient's up-role (the PM's PM or
the CEO) BEFORE the sweep logs/expiring it — not just logged-and-dropped.
Combined with H12 (Task 6), an inattentive PM can't both miss a blocker
and prevent main-pm from seeing it.

Re-escalation backoff: a static pile of stale notifications used to
re-escalate on *every* sweep tick (~1min) forever. `reescalation_decision`
(pure, in `foundation/policy/communications.py`) gates each tick behind a
per-notification exponential schedule + a hard retry cap.

Double-delivery race: `_persist_and_deliver`'s 60s dedup guard is a no-op for
`BLOCKER_ESCALATION` (not in `_LOOP_PRONE_TYPES`), so it can't backstop two
concurrent sweep ticks racing the same stale row — a compare-and-set claim
(`_claim_reescalation_slot`) is the real guard, exercised below by racing two
service instances against the same row.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from roboco.config import settings
from roboco.foundation.policy.communications import (
    ReescalationPolicy,
    reescalation_decision,
)
from roboco.models import NotificationPriority, NotificationType
from roboco.services.notification_delivery import NotificationDeliveryService
from sqlalchemy import Update


def _stale_notification(
    *,
    requires_ack: bool = True,
    acked: bool = False,
    recipient_id: UUID | None = None,
    reescalation_count: int = 0,
    last_reescalated_at: datetime | None = None,
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
    n.from_agent = uuid4()
    n.related_task_id = uuid4()
    n.reescalation_count = reescalation_count
    n.reescalation_delivered_count = 0  # no test needs a nonzero starting value
    n.last_reescalated_at = last_reescalated_at
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


def _assign_id_on_add(obj: Any) -> None:
    """`session.add` side effect: a real flush assigns the SQLAlchemy-default
    id; this mock has no engine to do that, so stand in for it here — without
    it `require_uuid(notification.id)` in `_persist_and_deliver` always raises
    on the freshly-built re-escalation row, making every "delivered" outcome
    in this suite look like a failure."""
    if getattr(obj, "id", None) is None:
        obj.id = uuid4()


def _session_returning(
    notifications: list[MagicMock], *, claim_succeeds: bool = True
) -> MagicMock:
    """A session whose SELECT (the sweep's stale-notifications query) returns
    `notifications`; every re-escalation CAS UPDATE (`_claim_reescalation_slot`)
    reports 1 row affected — the claim wins — unless `claim_succeeds` is False,
    simulating a concurrent sweep tick that already claimed this row's slot."""
    session = MagicMock()
    session.add = MagicMock(side_effect=_assign_id_on_add)
    session.flush = AsyncMock()

    select_result = MagicMock()
    select_result.scalars.return_value.all.return_value = notifications

    update_result = MagicMock()
    update_result.rowcount = 1 if claim_succeeds else 0

    async def _execute(statement: Any, *_args: Any, **_kwargs: Any) -> MagicMock:
        return update_result if isinstance(statement, Update) else select_result

    session.execute = AsyncMock(side_effect=_execute)
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
    assert notif.reescalation_delivered_count == 1  # the attempt was delivered


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
    logged-and-skipped, never raises). The attempt slot is still consumed
    (reescalation_count bumps) even though nothing was delivered — a broken
    chain burns attempts rather than looping forever; delivered stays 0,
    which is exactly the "route never worked" signal `_log_permanently_unacked`
    now carries."""
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
    assert notif.reescalation_count == 1  # attempt slot consumed regardless
    assert notif.reescalation_delivered_count == 0  # ...but nothing delivered


@pytest.mark.asyncio
async def test_sweep_cas_claim_prevents_double_delivery_race() -> None:
    """Two service instances (simulating two concurrent sweep ticks) race the
    same stale row. `_persist_and_deliver`'s 60s dedup guard cannot arbitrate
    this — BLOCKER_ESCALATION isn't a `_LOOP_PRONE_TYPES` member, so it's a
    no-op for this path. The CAS claim in `_claim_reescalation_slot` is what
    actually decides it: exactly one instance wins the guarded UPDATE and
    delivers; the loser (0 rows updated) skips delivery entirely, without
    raising."""
    recipient = _agent("be-pm")
    target = _agent("main-pm")
    notif = _stale_notification(
        requires_ack=True, acked=False, recipient_id=recipient.id
    )

    winner_session = _session_returning([notif], claim_succeeds=True)
    loser_session = _session_returning([notif], claim_succeeds=False)
    winner = _svc_with_agents(
        winner_session, recipient=recipient, escalation_target=target
    )
    loser = _svc_with_agents(
        loser_session, recipient=recipient, escalation_target=target
    )

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
        winner_count = await winner.sweep_expired_notifications()
        loser_count = await loser.sweep_expired_notifications()

    assert winner_count == 1
    assert loser_count == 1  # still stale + unacked from the loser's own view
    assert winner_session.add.call_count == 1  # won the claim, delivered
    loser_session.add.assert_not_called()  # lost the claim, never touched delivery


# =============================================================================
# reescalation_decision — pure schedule math
# =============================================================================


_DEFAULT_POLICY = ReescalationPolicy(base_seconds=3600, max_reescalations=5)


def test_reescalation_decision_first_fire_due_at_expiry() -> None:
    """count=0 (including a legacy row with no backoff state) is due the
    instant `now` reaches `expires_at` — preserves the original semantics."""
    expires_at = datetime(2026, 1, 1, tzinfo=UTC)
    assert (
        reescalation_decision(
            now=expires_at,
            expires_at=expires_at,
            count=0,
            last_reescalated_at=None,
            policy=_DEFAULT_POLICY,
        )
        == "due"
    )


def test_reescalation_decision_first_fire_not_due_before_expiry() -> None:
    expires_at = datetime(2026, 1, 1, tzinfo=UTC)
    assert (
        reescalation_decision(
            now=expires_at - timedelta(seconds=1),
            expires_at=expires_at,
            count=0,
            last_reescalated_at=None,
            policy=_DEFAULT_POLICY,
        )
        == "wait"
    )


def test_reescalation_decision_backoff_doubles() -> None:
    """count=2 waits 2*base (2h at the default base) from the last fire."""
    last = datetime(2026, 1, 1, tzinfo=UTC)
    expires_at = last - timedelta(hours=3)
    not_yet = reescalation_decision(
        now=last + timedelta(hours=2) - timedelta(seconds=1),
        expires_at=expires_at,
        count=2,
        last_reescalated_at=last,
        policy=_DEFAULT_POLICY,
    )
    due = reescalation_decision(
        now=last + timedelta(hours=2),
        expires_at=expires_at,
        count=2,
        last_reescalated_at=last,
        policy=_DEFAULT_POLICY,
    )
    assert not_yet == "wait"
    assert due == "due"


def test_reescalation_decision_interval_capped_at_24h() -> None:
    """However high `count` climbs (a raised max_reescalations), the wait
    between attempts never exceeds 24h."""
    last = datetime(2026, 1, 1, tzinfo=UTC)
    expires_at = last - timedelta(days=1)
    policy = ReescalationPolicy(base_seconds=3600, max_reescalations=20)
    # Uncapped this would be base*2**8 = 256h; capped it's 24h.
    not_yet = reescalation_decision(
        now=last + timedelta(hours=24) - timedelta(seconds=1),
        expires_at=expires_at,
        count=9,
        last_reescalated_at=last,
        policy=policy,
    )
    due = reescalation_decision(
        now=last + timedelta(hours=24),
        expires_at=expires_at,
        count=9,
        last_reescalated_at=last,
        policy=policy,
    )
    assert not_yet == "wait"
    assert due == "due"


def test_reescalation_decision_capped_past_max_regardless_of_timing() -> None:
    """count >= max_reescalations is always "capped", even if the schedule
    math would otherwise say a re-escalation is overdue."""
    last = datetime(2026, 1, 1, tzinfo=UTC)
    assert (
        reescalation_decision(
            now=last + timedelta(days=365),
            expires_at=last - timedelta(hours=1),
            count=5,
            last_reescalated_at=last,
            policy=_DEFAULT_POLICY,
        )
        == "capped"
    )


# =============================================================================
# sweep_expired_notifications — backoff integration
# =============================================================================


@pytest.mark.asyncio
async def test_sweep_backoff_does_not_refire_within_the_interval() -> None:
    """A row re-escalates once, then a same-tick-ish second sweep (interval
    not elapsed) does not re-escalate again — and its schedule state (count,
    last_reescalated_at) is stamped on the notification after the first."""
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
        first = await svc.sweep_expired_notifications()
        assert notif.reescalation_count == 1
        assert notif.last_reescalated_at is not None
        assert session.add.call_count == 1

        second = await svc.sweep_expired_notifications()

    assert first == 1
    assert second == 1  # still stale + unacked
    assert session.add.call_count == 1  # no second re-escalation this soon


@pytest.mark.asyncio
async def test_sweep_capped_row_never_re_escalates_again() -> None:
    """A row already at the retry cap is skipped forever — no re-escalation,
    no repeat 'permanently unacked' log — but still counts as stale+unacked."""
    recipient = _agent("be-pm")
    target = _agent("main-pm")
    capped_count = settings.notification_max_reescalations
    notif = _stale_notification(
        requires_ack=True,
        acked=False,
        recipient_id=recipient.id,
        reescalation_count=capped_count,
        last_reescalated_at=datetime.now(UTC) - timedelta(days=1),
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

    assert count == 1  # still stale + unacked
    session.add.assert_not_called()
    assert notif.reescalation_count == capped_count  # untouched — no further attempts
