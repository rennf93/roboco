"""NotificationDeliveryService.notify_ceo_of_librarian_drafts — the drafts
ride the EXISTING pending-playbook curation queue, not a new one, so this
must never render an actionable Telegram button, and "librarian" must stay
out of the callback/deep-link surface an approve/reject kind rides. Mirrors
test_notification_delivery_sentinel.py."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.models.base import NotificationType
from roboco.services.notification_delivery import NotificationDeliveryService
from roboco.services.telegram_inbound import (
    _DEEP_LINK_PATH,
    _KIND_DISPLAY,
    _VALID_KINDS,
)


def test_librarian_is_display_only_never_an_approve_reject_kind() -> None:
    """ "librarian" gets a display label (render_queue_item_text's fallback
    would otherwise use a generic emoji+title-case), but it must never join
    the approve/reject callback surface — parse_callback refuses any kind
    outside _VALID_KINDS, and build_action_keyboard's _DEEP_LINK_PATH lookup
    would KeyError if ever called for it (proof no code path can route a
    librarian callback even by accident)."""
    assert "librarian" in _KIND_DISPLAY
    assert "librarian" not in _VALID_KINDS
    assert "librarian" not in _DEEP_LINK_PATH


@pytest.mark.asyncio
async def test_notify_ceo_of_librarian_drafts_sends_no_action_keyboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Telegram push carries no reply_markup — the drafts ride the
    existing playbook queue, nothing here to approve/reject through a
    button."""
    svc = NotificationDeliveryService(MagicMock())
    monkeypatch.setattr(
        svc, "_get_ceo_agent", AsyncMock(return_value=MagicMock(id=uuid4()))
    )
    monkeypatch.setattr(svc, "_persist_and_deliver", AsyncMock())
    send = AsyncMock()
    monkeypatch.setattr(svc, "_send_telegram_deferred", send)
    task = MagicMock(assigned_to=uuid4())

    await svc.notify_ceo_of_librarian_drafts(
        task=task,
        task_id=uuid4(),
        titles=["Verify venv freshness before gate"],
    )

    send.assert_awaited_once()
    call = send.await_args
    assert call is not None
    assert call.kwargs["reply_markup"] is None
    assert "Verify venv freshness before gate" in call.kwargs["text"]


@pytest.mark.asyncio
async def test_notify_ceo_of_librarian_drafts_persists_a_normal_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In-app notification is a normal ALERT (mirrors
    notify_ceo_of_sentinel_report) — not an ack-required APPROVAL, since
    nothing here needs a CEO decision through a queue of its own."""
    svc = NotificationDeliveryService(MagicMock())
    monkeypatch.setattr(
        svc, "_get_ceo_agent", AsyncMock(return_value=MagicMock(id=uuid4()))
    )
    persist = AsyncMock()
    monkeypatch.setattr(svc, "_persist_and_deliver", persist)
    monkeypatch.setattr(svc, "_send_telegram_deferred", AsyncMock())
    task = MagicMock(assigned_to=uuid4())

    await svc.notify_ceo_of_librarian_drafts(
        task=task, task_id=uuid4(), titles=["A title"]
    )

    persist.assert_awaited_once()
    call = persist.await_args
    assert call is not None
    notification = call.args[0]
    assert notification.type == NotificationType.ALERT


@pytest.mark.asyncio
async def test_notify_ceo_of_librarian_drafts_no_ceo_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = NotificationDeliveryService(MagicMock())
    monkeypatch.setattr(svc, "_get_ceo_agent", AsyncMock(return_value=None))
    persist = AsyncMock()
    send = AsyncMock()
    monkeypatch.setattr(svc, "_persist_and_deliver", persist)
    monkeypatch.setattr(svc, "_send_telegram_deferred", send)

    await svc.notify_ceo_of_librarian_drafts(
        task=MagicMock(), task_id=uuid4(), titles=["A title"]
    )

    persist.assert_not_awaited()
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_notify_ceo_of_librarian_drafts_names_every_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = NotificationDeliveryService(MagicMock())
    monkeypatch.setattr(
        svc, "_get_ceo_agent", AsyncMock(return_value=MagicMock(id=uuid4()))
    )
    persist = AsyncMock()
    monkeypatch.setattr(svc, "_persist_and_deliver", persist)
    monkeypatch.setattr(svc, "_send_telegram_deferred", AsyncMock())
    task = MagicMock(assigned_to=uuid4())

    await svc.notify_ceo_of_librarian_drafts(
        task=task, task_id=uuid4(), titles=["First playbook", "Second playbook"]
    )

    call = persist.await_args
    assert call is not None
    notification = call.args[0]
    assert "First playbook" in notification.body
    assert "Second playbook" in notification.body
