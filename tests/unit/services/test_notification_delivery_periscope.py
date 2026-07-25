"""NotificationDeliveryService.notify_ceo_of_periscope_brief — a report has
no approve/reject verb, so this must never render an actionable Telegram
button, and "periscope" must stay out of the callback/deep-link surface an
approve/reject kind rides."""

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


def test_periscope_is_display_only_never_an_approve_reject_kind() -> None:
    """ "periscope" gets a display label (render_queue_item_text's fallback
    would otherwise use a generic emoji+title-case), but it must never join
    the approve/reject callback surface — parse_callback refuses any kind
    outside _VALID_KINDS, and build_action_keyboard's _DEEP_LINK_PATH lookup
    would KeyError if ever called for it (proof no code path can route a
    periscope callback even by accident)."""
    assert "periscope" in _KIND_DISPLAY
    assert "periscope" not in _VALID_KINDS
    assert "periscope" not in _DEEP_LINK_PATH


@pytest.mark.asyncio
async def test_notify_ceo_of_periscope_brief_sends_no_action_keyboard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Telegram push carries no reply_markup — a report has nothing to
    approve/reject through a button."""
    svc = NotificationDeliveryService(MagicMock())
    monkeypatch.setattr(
        svc, "_get_ceo_agent", AsyncMock(return_value=MagicMock(id=uuid4()))
    )
    monkeypatch.setattr(svc, "_persist_and_deliver", AsyncMock())
    send = AsyncMock()
    monkeypatch.setattr(svc, "_send_telegram_deferred", send)
    task = MagicMock(assigned_to=uuid4())

    await svc.notify_ceo_of_periscope_brief(
        task=task,
        task_id=uuid4(),
        headline="A rival tool shipped agentic PR review this week",
    )

    send.assert_awaited_once()
    call = send.await_args
    assert call is not None
    assert call.kwargs["reply_markup"] is None
    assert "A rival tool shipped agentic PR review this week" in call.kwargs["text"]


@pytest.mark.asyncio
async def test_notify_ceo_of_periscope_brief_persists_a_normal_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In-app notification is a normal ALERT (mirrors notify_ceo_of_
    completion) — not an ack-required APPROVAL, since nothing here needs a
    CEO decision through a queue."""
    svc = NotificationDeliveryService(MagicMock())
    monkeypatch.setattr(
        svc, "_get_ceo_agent", AsyncMock(return_value=MagicMock(id=uuid4()))
    )
    persist = AsyncMock()
    monkeypatch.setattr(svc, "_persist_and_deliver", persist)
    monkeypatch.setattr(svc, "_send_telegram_deferred", AsyncMock())
    task = MagicMock(assigned_to=uuid4())

    await svc.notify_ceo_of_periscope_brief(
        task=task, task_id=uuid4(), headline="A headline"
    )

    persist.assert_awaited_once()
    call = persist.await_args
    assert call is not None
    notification = call.args[0]
    assert notification.type == NotificationType.ALERT


@pytest.mark.asyncio
async def test_notify_ceo_of_periscope_brief_no_ceo_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = NotificationDeliveryService(MagicMock())
    monkeypatch.setattr(svc, "_get_ceo_agent", AsyncMock(return_value=None))
    persist = AsyncMock()
    send = AsyncMock()
    monkeypatch.setattr(svc, "_persist_and_deliver", persist)
    monkeypatch.setattr(svc, "_send_telegram_deferred", send)

    await svc.notify_ceo_of_periscope_brief(
        task=MagicMock(), task_id=uuid4(), headline="A headline"
    )

    persist.assert_not_awaited()
    send.assert_not_awaited()
