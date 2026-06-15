"""roboco.services.secretary — directive gate + execution (mocked deps)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.db.tables import SecretaryDirectiveTable
from roboco.models.secretary import DirectiveKind, DirectiveStatus
from roboco.services import secretary as sec_module
from roboco.services.base import ValidationError
from roboco.services.secretary import SecretaryService


def _session() -> MagicMock:
    s = MagicMock()
    s.add = MagicMock()
    s.flush = AsyncMock()
    return s


def _patch(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    msg = MagicMock()
    msg.post_to_channel = AsyncMock()
    monkeypatch.setattr(sec_module, "get_messaging_service", lambda _s: msg)
    goals = MagicMock()
    goals.upsert = AsyncMock()
    monkeypatch.setattr(sec_module, "get_company_goals_service", lambda _s: goals)
    pitch = MagicMock()
    pitch.approve = AsyncMock()
    monkeypatch.setattr(sec_module, "get_pitch_service", lambda _s: pitch)
    task = MagicMock()
    task.approve_and_start = AsyncMock()
    task.admin_set_status = AsyncMock()
    monkeypatch.setattr(sec_module, "get_task_service", lambda _s: task)
    notifier = MagicMock()
    notifier.send_ack_notification = AsyncMock()
    monkeypatch.setattr(
        "roboco.services.notification.NotificationService", lambda: notifier
    )
    return {
        "msg": msg,
        "goals": goals,
        "pitch": pitch,
        "task": task,
        "notifier": notifier,
    }


def _pending(kind: DirectiveKind, payload: dict[str, Any]) -> SecretaryDirectiveTable:
    return SecretaryDirectiveTable(
        id=uuid4(),
        kind=kind.value,
        payload=payload,
        status=DirectiveStatus.PENDING.value,
        requested_by=uuid4(),
    )


@pytest.mark.asyncio
async def test_relay_executes_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    svcs = _patch(monkeypatch)
    svc = SecretaryService(_session())
    row = await svc.submit_directive(
        DirectiveKind.RELAY_MESSAGE,
        {"channel": "all-hands", "text": "standup at 10"},
        uuid4(),
    )
    assert row.status == DirectiveStatus.EXECUTED.value
    svcs["msg"].post_to_channel.assert_awaited_once()
    svcs["notifier"].send_ack_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_gated_charter_queues_and_notifies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svcs = _patch(monkeypatch)
    svc = SecretaryService(_session())
    row = await svc.submit_directive(
        DirectiveKind.UPDATE_CHARTER, {"charter": {"north_star": "Win"}}, uuid4()
    )
    assert row.status == DirectiveStatus.PENDING.value
    svcs["goals"].upsert.assert_not_awaited()
    svcs["notifier"].send_ack_notification.assert_awaited_once()


@pytest.mark.asyncio
async def test_confirm_charter_executes(monkeypatch: pytest.MonkeyPatch) -> None:
    svcs = _patch(monkeypatch)
    svc = SecretaryService(_session())
    row = _pending(DirectiveKind.UPDATE_CHARTER, {"charter": {"north_star": "Win"}})
    monkeypatch.setattr(svc, "get_directive", AsyncMock(return_value=row))
    out = await svc.confirm_directive(row.id, uuid4())
    assert out.status == DirectiveStatus.EXECUTED.value
    svcs["goals"].upsert.assert_awaited_once()


@pytest.mark.asyncio
async def test_confirm_control_task_start(monkeypatch: pytest.MonkeyPatch) -> None:
    svcs = _patch(monkeypatch)
    svc = SecretaryService(_session())
    row = _pending(
        DirectiveKind.CONTROL_TASK, {"task_id": str(uuid4()), "action": "start"}
    )
    monkeypatch.setattr(svc, "get_directive", AsyncMock(return_value=row))
    out = await svc.confirm_directive(row.id, uuid4())
    assert out.status == DirectiveStatus.EXECUTED.value
    svcs["task"].approve_and_start.assert_awaited_once()


@pytest.mark.asyncio
async def test_confirm_approve_pitch(monkeypatch: pytest.MonkeyPatch) -> None:
    svcs = _patch(monkeypatch)
    svc = SecretaryService(_session())
    row = _pending(DirectiveKind.APPROVE_PITCH, {"pitch_id": str(uuid4())})
    monkeypatch.setattr(svc, "get_directive", AsyncMock(return_value=row))
    out = await svc.confirm_directive(row.id, uuid4())
    assert out.status == DirectiveStatus.EXECUTED.value
    svcs["pitch"].approve.assert_awaited_once()


@pytest.mark.asyncio
async def test_announce_queues_then_confirm_posts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svcs = _patch(monkeypatch)
    svc = SecretaryService(_session())
    row = await svc.submit_directive(
        DirectiveKind.ANNOUNCE, {"text": "we shipped v1"}, uuid4()
    )
    assert row.status == DirectiveStatus.PENDING.value
    monkeypatch.setattr(svc, "get_directive", AsyncMock(return_value=row))
    out = await svc.confirm_directive(row.id, uuid4())
    assert out.status == DirectiveStatus.EXECUTED.value
    svcs["msg"].post_to_channel.assert_awaited_once()


@pytest.mark.asyncio
async def test_reject_sets_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch)
    svc = SecretaryService(_session())
    row = _pending(DirectiveKind.ANNOUNCE, {"text": "x"})
    monkeypatch.setattr(svc, "get_directive", AsyncMock(return_value=row))
    out = await svc.reject_directive(row.id, uuid4(), "not now")
    assert out.status == DirectiveStatus.REJECTED.value
    assert out.result == "not now"


@pytest.mark.asyncio
async def test_missing_payload_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch)
    svc = SecretaryService(_session())
    with pytest.raises(ValidationError):
        await svc.submit_directive(
            DirectiveKind.RELAY_MESSAGE, {"channel": "x"}, uuid4()
        )


@pytest.mark.asyncio
async def test_bad_task_action_fails_directive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch(monkeypatch)
    svc = SecretaryService(_session())
    row = _pending(
        DirectiveKind.CONTROL_TASK, {"task_id": str(uuid4()), "action": "explode"}
    )
    monkeypatch.setattr(svc, "get_directive", AsyncMock(return_value=row))
    out = await svc.confirm_directive(row.id, uuid4())
    assert out.status == DirectiveStatus.FAILED.value
