"""Tests for PM Choreographer methods.

Covers: triage, triage_all, unblock, complete, escalate_up.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_deps(**overrides):
    base = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    base.update(overrides)
    repo = base["evidence_repo"]
    for method in (
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
        "journal_highlights_for_task",
    ):
        getattr(repo, method).return_value = []
    return ChoreographerDeps(**base)


@pytest.mark.asyncio
async def test_cell_pm_triage_returns_blocked_first():
    pm_id = uuid4()
    blocked_task = MagicMock(id=uuid4(), status="blocked", title="b", team="backend")
    pending_task = MagicMock(
        id=uuid4(), status="awaiting_pm_review", title="p", team="backend"
    )
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.list_blocked_for_team.return_value = [blocked_task]
    task_svc.list_awaiting_pm_review_for_team.return_value = [pending_task]
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.triage(pm_id)
    body = env.as_dict()
    assert body["task_id"] == str(blocked_task.id)
    assert "unblock" in body["next"].lower()


@pytest.mark.asyncio
async def test_cell_pm_triage_returns_awaiting_review_when_no_blocked():
    pm_id = uuid4()
    pending_task = MagicMock(id=uuid4(), status="awaiting_pm_review", team="backend")
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.list_blocked_for_team.return_value = []
    task_svc.list_awaiting_pm_review_for_team.return_value = [pending_task]
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.triage(pm_id)
    body = env.as_dict()
    assert body["task_id"] == str(pending_task.id)
    assert "complete" in body["next"]


@pytest.mark.asyncio
async def test_cell_pm_triage_returns_idle_when_no_work():
    pm_id = uuid4()
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    task_svc.list_blocked_for_team.return_value = []
    task_svc.list_awaiting_pm_review_for_team.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.triage(pm_id)
    body = env.as_dict()
    assert body["status"] == "idle"


@pytest.mark.asyncio
async def test_main_pm_triage_all_includes_cross_team():
    pm_id = uuid4()
    blocked = MagicMock(id=uuid4(), status="blocked", team="backend", title="x")
    task_svc = AsyncMock()
    task_svc.list_blocked_all_teams.return_value = [blocked]
    task_svc.list_awaiting_main_pm_all.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.triage_all(pm_id)
    body = env.as_dict()
    assert body["error"] is None
    assert body["task_id"] == str(blocked.id)


@pytest.mark.asyncio
async def test_main_pm_triage_all_returns_idle():
    pm_id = uuid4()
    task_svc = AsyncMock()
    task_svc.list_blocked_all_teams.return_value = []
    task_svc.list_awaiting_main_pm_all.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.triage_all(pm_id)
    assert env.status == "idle"


@pytest.mark.asyncio
async def test_unblock_restores_pre_block_state():
    pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="blocked",
        pre_block_state="awaiting_documentation",
        pre_block_assignee=uuid4(),
        pre_block_metadata={"some_field": "x"},
    )
    after = MagicMock(
        **{
            **t.__dict__,
            "status": "awaiting_documentation",
            "assigned_to": t.pre_block_assignee,
        },
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.unblock_with_restore.return_value = after
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.unblock(pm_id, task_id, restore=True)
    assert env.error is None
    assert env.status == "awaiting_documentation"
    task_svc.unblock_with_restore.assert_awaited_once_with(pm_id, task_id, restore=True)


@pytest.mark.asyncio
async def test_unblock_default_restores():
    pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="blocked",
        pre_block_state="awaiting_qa",
        pre_block_assignee=uuid4(),
        pre_block_metadata={},
    )
    after = MagicMock(**{**t.__dict__, "status": "awaiting_qa"})
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.unblock_with_restore.return_value = after
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    # restore omitted -> defaults to True
    env = await c.unblock(pm_id, task_id)
    assert env.status == "awaiting_qa"


@pytest.mark.asyncio
async def test_unblock_blocks_without_journal_decision():
    pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="blocked")
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = False
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.unblock(pm_id, task_id)
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "journal:decision" in body["missing"]


@pytest.mark.asyncio
async def test_unblock_wrong_state_returns_invalid_state():
    pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="in_progress")
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.unblock(pm_id, task_id)
    body = env.as_dict()
    assert body["error"] == "invalid_state"


@pytest.mark.asyncio
async def test_unblock_restore_false_returns_legacy_message():
    pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="blocked",
        pre_block_state="awaiting_qa",
        pre_block_assignee=uuid4(),
        pre_block_metadata={},
    )
    after = MagicMock(**{**t.__dict__, "status": "in_progress"})
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.unblock_with_restore.return_value = after
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.unblock(pm_id, task_id, restore=False)
    body = env.as_dict()
    assert body["status"] == "in_progress"
    assert "re-engage" in body["next"].lower()
