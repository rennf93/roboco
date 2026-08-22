"""NotificationDeliveryService.notify_ceo_of_queue_item: the chokepoint every
Board Program (Mirror, Pest Control, Spackle, Scales, Dogfood, roadmap) plus
release/xpost/video drafts uses to push a held item at the CEO. It used to
send only the Telegram DM and never write an in-app row at all, so the panel
notification list stayed empty while Telegram kept firing. This file pins
both halves: the in-app row now exists and is CEO-addressed with the right
shape, and the Telegram send is unchanged and independent of the new half.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.config import settings as cfg
from roboco.models.base import NotificationPriority, NotificationType
from roboco.services.notification_delivery import NotificationDeliveryService


@pytest.mark.asyncio
async def test_notify_ceo_of_queue_item_persists_an_in_app_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fails without the fix: the old implementation never called
    `_persist_and_deliver` at all, so this assertion is the regression pin."""
    ceo_id = uuid4()
    svc = NotificationDeliveryService(MagicMock())
    monkeypatch.setattr(
        svc, "_get_ceo_agent", AsyncMock(return_value=MagicMock(id=ceo_id))
    )
    persist = AsyncMock()
    monkeypatch.setattr(svc, "_persist_and_deliver", persist)
    monkeypatch.setattr(svc, "_send_telegram_deferred", AsyncMock())

    await svc.notify_ceo_of_queue_item(
        kind="mirror", id8="abcd1234", extra="7", title="Landing page copy drift"
    )

    persist.assert_awaited_once()


@pytest.mark.asyncio
async def test_notify_ceo_of_queue_item_row_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """APPROVAL/HIGH, requires_ack, addressed to (and from) the CEO, exactly
    like notify_ceo_of_escalation/notify_ceo_of_pitch. The purpose-dedup
    guard is bypassed since distinct queue items share the same
    (from_agent, type, related_task_id) tuple and would otherwise collide."""
    ceo_id = uuid4()
    svc = NotificationDeliveryService(MagicMock())
    monkeypatch.setattr(
        svc, "_get_ceo_agent", AsyncMock(return_value=MagicMock(id=ceo_id))
    )
    persist = AsyncMock()
    monkeypatch.setattr(svc, "_persist_and_deliver", persist)
    monkeypatch.setattr(svc, "_send_telegram_deferred", AsyncMock())

    await svc.notify_ceo_of_queue_item(
        kind="pest_control", id8="deadbeef", title="Race condition in claim path"
    )

    persist.assert_awaited_once()
    call = persist.await_args
    assert call is not None
    notification = call.args[0]
    assert notification.type == NotificationType.APPROVAL
    assert notification.priority == NotificationPriority.HIGH
    assert notification.to_agents == [ceo_id]
    assert notification.from_agent == ceo_id
    assert notification.requires_ack is True
    assert "Race condition in claim path" in notification.subject
    assert call.kwargs.get("bypass_purpose_dedup") is True


@pytest.mark.asyncio
async def test_notify_ceo_of_queue_item_carries_related_task_id_and_expiry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEFECT 2 regression: without related_task_id the row can never be
    reached by resolve_terminal_task_escalations's JOIN (it never auto-acks
    once the underlying item is decided), and without expires_at the
    re-escalation/permanently-unacked backstop never fires either — the
    pending-ack badge grows monotonically and never reflects real work."""
    task_id = uuid4()
    svc = NotificationDeliveryService(MagicMock())
    monkeypatch.setattr(
        svc, "_get_ceo_agent", AsyncMock(return_value=MagicMock(id=uuid4()))
    )
    persist = AsyncMock()
    monkeypatch.setattr(svc, "_persist_and_deliver", persist)
    monkeypatch.setattr(svc, "_send_telegram_deferred", AsyncMock())

    await svc.notify_ceo_of_queue_item(
        kind="release",
        id8="a1b2c3d4",
        title="v1.0.0 ready",
        related_task_id=task_id,
    )

    call = persist.await_args
    assert call is not None
    notification = call.args[0]
    assert notification.related_task_id == task_id
    assert notification.expires_at is not None


@pytest.mark.asyncio
async def test_notify_ceo_of_queue_item_expiry_respects_ttl_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """notification_ack_ttl_hours=0 disables stamping, same as every other
    ack-required notification in this file — never a hard requirement, just
    consistent with the existing convention."""
    monkeypatch.setattr(cfg, "notification_ack_ttl_hours", 0)
    svc = NotificationDeliveryService(MagicMock())
    monkeypatch.setattr(
        svc, "_get_ceo_agent", AsyncMock(return_value=MagicMock(id=uuid4()))
    )
    persist = AsyncMock()
    monkeypatch.setattr(svc, "_persist_and_deliver", persist)
    monkeypatch.setattr(svc, "_send_telegram_deferred", AsyncMock())

    await svc.notify_ceo_of_queue_item(
        kind="release", id8="a1b2c3d4", title="v1.0.0 ready", related_task_id=uuid4()
    )

    call = persist.await_args
    assert call is not None
    notification = call.args[0]
    assert notification.expires_at is None


@pytest.mark.asyncio
async def test_notify_ceo_of_queue_item_telegram_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Telegram half keeps rendering with the shared /queue renderer and
    its Approve/Reject/Open keyboard, byte-for-byte as before this fix."""
    svc = NotificationDeliveryService(MagicMock())
    monkeypatch.setattr(
        svc, "_get_ceo_agent", AsyncMock(return_value=MagicMock(id=uuid4()))
    )
    monkeypatch.setattr(svc, "_persist_and_deliver", AsyncMock())
    send = AsyncMock()
    monkeypatch.setattr(svc, "_send_telegram_deferred", send)

    await svc.notify_ceo_of_queue_item(
        kind="spackle", id8="0badf00d", extra="3", title="No panel tab for X"
    )

    send.assert_awaited_once()
    call = send.await_args
    assert call is not None
    assert call.kwargs["reply_markup"] is not None
    assert "No panel tab for X" in call.kwargs["text"]


@pytest.mark.asyncio
async def test_notify_ceo_of_queue_item_no_ceo_skips_in_app_but_still_sends_telegram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No AgentTable CEO row only skips the in-app half: Telegram sends off
    stored credentials, not that row, so a missing CEO agent must not
    silently drop the Telegram push too (that would be a regression)."""
    svc = NotificationDeliveryService(MagicMock())
    monkeypatch.setattr(svc, "_get_ceo_agent", AsyncMock(return_value=None))
    persist = AsyncMock()
    send = AsyncMock()
    monkeypatch.setattr(svc, "_persist_and_deliver", persist)
    monkeypatch.setattr(svc, "_send_telegram_deferred", send)

    await svc.notify_ceo_of_queue_item(
        kind="dogfood", id8="12345678", title="Broken empty state"
    )

    persist.assert_not_awaited()
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_notify_ceo_of_queue_item_persist_failure_does_not_block_telegram(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither channel may take the other down: a DB error persisting the
    in-app row is caught and logged, and the Telegram push still fires."""
    svc = NotificationDeliveryService(MagicMock())
    monkeypatch.setattr(
        svc, "_get_ceo_agent", AsyncMock(return_value=MagicMock(id=uuid4()))
    )
    monkeypatch.setattr(
        svc, "_persist_and_deliver", AsyncMock(side_effect=RuntimeError("db down"))
    )
    send = AsyncMock()
    monkeypatch.setattr(svc, "_send_telegram_deferred", send)

    await svc.notify_ceo_of_queue_item(
        kind="scales", id8="f00dcafe", title="Reprioritize stale backlog item"
    )

    send.assert_awaited_once()
