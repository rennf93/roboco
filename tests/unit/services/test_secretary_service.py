"""roboco.services.secretary — directive gate + execution (mocked deps)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.db.tables import SecretaryDirectiveTable
from roboco.models.base import Complexity, TaskNature, TaskStatus, Team
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
    goals = MagicMock()
    goals.upsert = AsyncMock()
    monkeypatch.setattr(sec_module, "get_company_goals_service", lambda _s: goals)
    pitch = MagicMock()
    pitch.approve = AsyncMock()
    monkeypatch.setattr(sec_module, "get_pitch_service", lambda _s: pitch)
    task = MagicMock()
    task.approve_and_start = AsyncMock()
    task.admin_set_status = AsyncMock()
    task.update = AsyncMock()
    task.get = AsyncMock(return_value=None)
    task.reassign = AsyncMock()
    task.reassign_active_claim = AsyncMock()
    monkeypatch.setattr(sec_module, "get_task_service", lambda _s: task)
    agent_lookup = AsyncMock(return_value=None)
    monkeypatch.setattr(sec_module, "get_agent_by_slug", agent_lookup)
    notifier = MagicMock()
    notifier.send_ack_notification = AsyncMock()
    notifier.send_broadcast_notification = AsyncMock()
    monkeypatch.setattr(
        "roboco.services.notification.NotificationService", lambda: notifier
    )
    monkeypatch.setattr(sec_module, "NotificationService", lambda: notifier)
    return {
        "goals": goals,
        "pitch": pitch,
        "task": task,
        "notifier": notifier,
        "agent_lookup": agent_lookup,
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
        {"text": "standup at 10"},
        uuid4(),
    )
    assert row.status == DirectiveStatus.EXECUTED.value
    svcs["notifier"].send_broadcast_notification.assert_awaited_once()


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
    svcs["notifier"].send_broadcast_notification.assert_awaited_once()


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


@pytest.mark.asyncio
async def test_confirm_control_task_edit_updates_allowlisted_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Secretary can MODIFY a task's content fields on CEO confirmation
    (reMarkable item) — restricted to the safe allowlist."""
    svcs = _patch(monkeypatch)
    svc = SecretaryService(_session())
    tid = uuid4()
    row = _pending(
        DirectiveKind.CONTROL_TASK,
        {
            "task_id": str(tid),
            "action": "edit",
            "fields": {
                "title": "Sharper title",
                "priority": 1,
                "description": "Clarified description from the CEO chat.",
            },
        },
    )
    monkeypatch.setattr(svc, "get_directive", AsyncMock(return_value=row))
    out = await svc.confirm_directive(row.id, uuid4())
    assert out.status == DirectiveStatus.EXECUTED.value
    svcs["task"].update.assert_awaited_once()
    _, kwargs = svcs["task"].update.await_args
    assert kwargs["title"] == "Sharper title"
    assert kwargs["priority"] == 1


@pytest.mark.asyncio
async def test_control_task_edit_rejects_non_allowlisted_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Status/ownership/git fields never ride an edit — those have their own
    audited paths (override, reassign)."""
    svcs = _patch(monkeypatch)
    svc = SecretaryService(_session())
    row = _pending(
        DirectiveKind.CONTROL_TASK,
        {
            "task_id": str(uuid4()),
            "action": "edit",
            "fields": {"status": "completed"},
        },
    )
    monkeypatch.setattr(svc, "get_directive", AsyncMock(return_value=row))
    out = await svc.confirm_directive(row.id, uuid4())
    assert out.status == DirectiveStatus.FAILED.value
    svcs["task"].update.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirm_control_task_edit_extended_content_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Secretary FULL: team/estimated_complexity/nature ride the edit too,
    coerced into their proper enums before hitting TaskService.update."""
    svcs = _patch(monkeypatch)
    svc = SecretaryService(_session())
    tid = uuid4()
    row = _pending(
        DirectiveKind.CONTROL_TASK,
        {
            "task_id": str(tid),
            "action": "edit",
            "fields": {
                "team": "frontend",
                "estimated_complexity": "high",
                "nature": "technical",
            },
        },
    )
    monkeypatch.setattr(svc, "get_directive", AsyncMock(return_value=row))
    out = await svc.confirm_directive(row.id, uuid4())
    assert out.status == DirectiveStatus.EXECUTED.value
    svcs["task"].update.assert_awaited_once()
    _, kwargs = svcs["task"].update.await_args
    assert kwargs["team"] == Team.FRONTEND
    assert kwargs["estimated_complexity"] == Complexity.HIGH
    assert kwargs["nature"] == TaskNature.TECHNICAL


@pytest.mark.asyncio
async def test_confirm_control_task_edit_bad_enum_value_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svcs = _patch(monkeypatch)
    svc = SecretaryService(_session())
    row = _pending(
        DirectiveKind.CONTROL_TASK,
        {
            "task_id": str(uuid4()),
            "action": "edit",
            "fields": {"team": "not-a-real-team"},
        },
    )
    monkeypatch.setattr(svc, "get_directive", AsyncMock(return_value=row))
    out = await svc.confirm_directive(row.id, uuid4())
    assert out.status == DirectiveStatus.FAILED.value
    svcs["task"].update.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirm_control_task_edit_reassigns_active_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reassigning an active (claimed/in_progress) task goes through
    ``reassign_active_claim`` so the new assignee reseeds its heartbeat and
    isn't immediately stale to the reaper — not a naive setattr."""
    svcs = _patch(monkeypatch)
    new_assignee = uuid4()
    svcs["task"].get = AsyncMock(
        return_value=SimpleNamespace(status=TaskStatus.IN_PROGRESS)
    )
    svcs["task"].reassign_active_claim = AsyncMock(
        return_value=SimpleNamespace(status=TaskStatus.IN_PROGRESS)
    )
    svc = SecretaryService(_session())
    tid = uuid4()
    row = _pending(
        DirectiveKind.CONTROL_TASK,
        {
            "task_id": str(tid),
            "action": "edit",
            "fields": {"assigned_to": str(new_assignee)},
        },
    )
    monkeypatch.setattr(svc, "get_directive", AsyncMock(return_value=row))
    out = await svc.confirm_directive(row.id, uuid4())
    assert out.status == DirectiveStatus.EXECUTED.value
    svcs["task"].reassign_active_claim.assert_awaited_once_with(tid, new_assignee)
    svcs["task"].reassign.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirm_control_task_edit_reassigns_non_active_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-active task (e.g. pending) reassigns through the general
    ``reassign`` path — no heartbeat to reseed."""
    svcs = _patch(monkeypatch)
    new_assignee = uuid4()
    svcs["task"].get = AsyncMock(
        return_value=SimpleNamespace(status=TaskStatus.PENDING)
    )
    svc = SecretaryService(_session())
    tid = uuid4()
    row = _pending(
        DirectiveKind.CONTROL_TASK,
        {
            "task_id": str(tid),
            "action": "edit",
            "fields": {"assigned_to": str(new_assignee)},
        },
    )
    monkeypatch.setattr(svc, "get_directive", AsyncMock(return_value=row))
    out = await svc.confirm_directive(row.id, uuid4())
    assert out.status == DirectiveStatus.EXECUTED.value
    svcs["task"].reassign.assert_awaited_once_with(tid, new_assignee)
    svcs["task"].reassign_active_claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirm_control_task_edit_reassigns_by_slug(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The CEO refers to agents by slug (e.g. 'be-dev-1'); the edit resolves
    it to a UUID the same way the REST PATCH path does."""
    svcs = _patch(monkeypatch)
    agent_row_id = uuid4()
    svcs["agent_lookup"].return_value = SimpleNamespace(id=agent_row_id)
    svcs["task"].get = AsyncMock(
        return_value=SimpleNamespace(status=TaskStatus.PENDING)
    )
    svc = SecretaryService(_session())
    tid = uuid4()
    row = _pending(
        DirectiveKind.CONTROL_TASK,
        {
            "task_id": str(tid),
            "action": "edit",
            "fields": {"assigned_to": "be-dev-1"},
        },
    )
    monkeypatch.setattr(svc, "get_directive", AsyncMock(return_value=row))
    out = await svc.confirm_directive(row.id, uuid4())
    assert out.status == DirectiveStatus.EXECUTED.value
    svcs["task"].reassign.assert_awaited_once_with(tid, agent_row_id)


@pytest.mark.asyncio
async def test_confirm_control_task_edit_unknown_assignee_slug_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svcs = _patch(monkeypatch)
    svc = SecretaryService(_session())
    row = _pending(
        DirectiveKind.CONTROL_TASK,
        {
            "task_id": str(uuid4()),
            "action": "edit",
            "fields": {"assigned_to": "no-such-agent"},
        },
    )
    monkeypatch.setattr(svc, "get_directive", AsyncMock(return_value=row))
    out = await svc.confirm_directive(row.id, uuid4())
    assert out.status == DirectiveStatus.FAILED.value
    svcs["task"].reassign.assert_not_awaited()
    svcs["task"].reassign_active_claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirm_control_task_edit_combines_fields_and_reassign(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single edit directive may both update content fields and reassign."""
    svcs = _patch(monkeypatch)
    new_assignee = uuid4()
    svcs["task"].get = AsyncMock(
        return_value=SimpleNamespace(status=TaskStatus.PENDING)
    )
    svc = SecretaryService(_session())
    tid = uuid4()
    row = _pending(
        DirectiveKind.CONTROL_TASK,
        {
            "task_id": str(tid),
            "action": "edit",
            "fields": {"title": "Renamed", "assigned_to": str(new_assignee)},
        },
    )
    monkeypatch.setattr(svc, "get_directive", AsyncMock(return_value=row))
    out = await svc.confirm_directive(row.id, uuid4())
    assert out.status == DirectiveStatus.EXECUTED.value
    svcs["task"].update.assert_awaited_once()
    _, kwargs = svcs["task"].update.await_args
    assert kwargs == {"title": "Renamed"}
    svcs["task"].reassign.assert_awaited_once_with(tid, new_assignee)


_SEEDED_PR_NUMBER = 42
_SEEDED_PROGRESS_UPDATE_COUNT = sec_module._MAX_PROGRESS_UPDATES + 10


@pytest.mark.asyncio
async def test_read_task_includes_full_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Secretary FULL read breadth: notes/progress/plan/pr fields join the
    brief identity fields that read_task already carried."""
    svcs = _patch(monkeypatch)
    tid = uuid4()
    fake_task = SimpleNamespace(
        id=tid,
        title="Ship the thing",
        status=TaskStatus.IN_PROGRESS,
        team="backend",
        assigned_to=uuid4(),
        description="A real description.",
        acceptance_criteria=["works"],
        priority=1,
        estimated_complexity="high",
        nature="technical",
        plan={"approach": "do it"},
        progress_updates=[
            {"message": f"update {i}"} for i in range(_SEEDED_PROGRESS_UPDATE_COUNT)
        ],
        dev_notes="dev note",
        qa_notes="qa note",
        auditor_notes="audit note",
        pr_reviewer_notes="pr note",
        doc_notes="doc note",
        quick_context="ctx",
        branch_name="feature/x",
        pr_number=_SEEDED_PR_NUMBER,
        pr_url="https://example.com/pr/42",
    )
    svcs["task"].get = AsyncMock(return_value=fake_task)
    svc = SecretaryService(_session())
    out = await svc.read_task(tid)
    assert out["title"] == "Ship the thing"
    assert out["plan"] == {"approach": "do it"}
    assert out["dev_notes"] == "dev note"
    assert out["pr_number"] == _SEEDED_PR_NUMBER
    assert out["branch_name"] == "feature/x"
    # Bounded: only the most recent entries survive.
    assert len(out["progress_updates"]) == sec_module._MAX_PROGRESS_UPDATES
    last_index = _SEEDED_PROGRESS_UPDATE_COUNT - 1
    assert out["progress_updates"][-1]["message"] == f"update {last_index}"
