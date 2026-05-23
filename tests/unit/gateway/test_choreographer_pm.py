"""Tests for PM Choreographer methods.

Covers: triage, triage_all, unblock, complete, escalate_up.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_deps(**overrides: Any) -> ChoreographerDeps:
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
    # C8: default-fresh journal:decision so PM-decision gate passes.
    # Tests that exercise the gate boundary stub their own value.
    # The check matches MagicMock and AsyncMock (the two default sentinel
    # types pytest's unittest.mock leaves on un-stubbed return_values).
    _ldef = base["journal"].latest_decision_at.return_value
    if type(_ldef).__name__ in ("MagicMock", "AsyncMock"):
        base["journal"].latest_decision_at.return_value = datetime.now(UTC)
    return ChoreographerDeps(**base)


@pytest.mark.asyncio
async def test_cell_pm_triage_returns_blocked_first() -> None:
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
async def test_cell_pm_triage_returns_awaiting_review_when_no_blocked() -> None:
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
async def test_cell_pm_triage_returns_idle_when_no_work() -> None:
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
async def test_main_pm_triage_all_includes_cross_team() -> None:
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
async def test_main_pm_triage_all_returns_idle() -> None:
    pm_id = uuid4()
    task_svc = AsyncMock()
    task_svc.list_blocked_all_teams.return_value = []
    task_svc.list_awaiting_main_pm_all.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.triage_all(pm_id)
    assert env.status == "idle"


@pytest.mark.asyncio
async def test_unblock_restores_pre_block_state() -> None:
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
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.unblock(pm_id, task_id, restore=True)
    assert env.error is None
    assert env.status == "awaiting_documentation"
    task_svc.unblock_with_restore.assert_awaited_once_with(pm_id, task_id, restore=True)


@pytest.mark.asyncio
async def test_unblock_default_restores() -> None:
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
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    # restore omitted -> defaults to True
    env = await c.unblock(pm_id, task_id)
    assert env.status == "awaiting_qa"


@pytest.mark.asyncio
async def test_unblock_blocks_without_journal_decision() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="blocked")
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = False
    journal_svc.latest_decision_at.return_value = None
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.unblock(pm_id, task_id)
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "journal:decision" in body["missing"]


@pytest.mark.asyncio
async def test_unblock_wrong_state_returns_invalid_state() -> None:
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
async def test_unblock_restore_false_returns_legacy_message() -> None:
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
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.unblock(pm_id, task_id, restore=False)
    body = env.as_dict()
    assert body["status"] == "in_progress"
    assert "re-engage" in body["next"].lower()


@pytest.mark.asyncio
async def test_cell_pm_complete_merges_then_completes() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    parent_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="awaiting_pm_review",
        assigned_to=pm_id,
        pr_number=8,
        branch_name="feature/backend/abc--def",
        parent_task_id=parent_id,
        team="backend",
    )
    after = MagicMock(**{**t.__dict__, "status": "completed"})
    # #181/#182: the merge target is the PARENT task's real branch_name —
    # here under a DIFFERENT team prefix, which the old parent_branch_for
    # would have mis-derived as feature/backend/abc.
    parent = MagicMock(
        id=parent_id, branch_name="feature/main_pm/abc", parent_task_id=None
    )
    task_svc = AsyncMock()
    task_svc.get.side_effect = lambda tid: parent if tid == parent_id else t
    task_svc.all_subtasks_terminal.return_value = True
    task_svc.cell_pm_complete.return_value = after
    git_svc = AsyncMock()
    git_svc.pr_merge.return_value = {"merged": True, "merge_commit_sha": "merge-abc"}
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, git=git_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.cell_pm_complete(pm_id, task_id, notes="reviewed and approved")
    assert env.error is None
    assert env.status == "completed"
    git_svc.pr_merge.assert_awaited_once_with(
        8, target="feature/main_pm/abc", actor_agent_id=pm_id
    )


@pytest.mark.asyncio
async def test_cell_pm_complete_blocks_if_subtasks_unfinished() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="awaiting_pm_review",
        assigned_to=pm_id,
        parent_task_id=uuid4(),
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.all_subtasks_terminal.return_value = False
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.cell_pm_complete(
        pm_id, task_id, notes="reviewed cell scope and approved merge"
    )
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "subtasks" in str(body["missing"]).lower()


@pytest.mark.asyncio
async def test_cell_pm_complete_requires_journal_decision() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="awaiting_pm_review",
        assigned_to=pm_id,
        parent_task_id=uuid4(),
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.all_subtasks_terminal.return_value = True
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = False
    journal_svc.latest_decision_at.return_value = None
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.cell_pm_complete(pm_id, task_id, notes="x")
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "journal:decision" in body["missing"]


@pytest.mark.asyncio
async def test_cell_pm_complete_no_pr_returns_invalid_state() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="awaiting_pm_review",
        assigned_to=pm_id,
        pr_number=None,
        branch_name="feature/backend/abc--def",
        parent_task_id=uuid4(),
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.all_subtasks_terminal.return_value = True
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.cell_pm_complete(
        pm_id, task_id, notes="reviewed cell scope and approved merge"
    )
    assert env.as_dict()["error"] == "invalid_state"


@pytest.mark.asyncio
async def test_cell_pm_complete_not_assigned_returns_not_authorized() -> None:
    pm_id = uuid4()
    other = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="awaiting_pm_review", assigned_to=other)
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.cell_pm_complete(pm_id, task_id, notes="x")
    assert env.as_dict()["error"] == "not_authorized"


@pytest.mark.asyncio
async def test_main_pm_complete_opens_master_pr_and_escalates() -> None:
    main_pm_id = uuid4()
    root_task_id = uuid4()
    t = MagicMock(
        id=root_task_id,
        status="awaiting_pm_review",
        assigned_to=main_pm_id,
        pr_number=None,
        branch_name="feature/backend/root123",
        parent_task_id=None,
        team="backend",
    )
    after = MagicMock(**{**t.__dict__, "status": "awaiting_ceo_approval"})
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.escalate_to_ceo.return_value = after
    task_svc.all_subtasks_terminal.return_value = True
    git_svc = AsyncMock()
    git_svc.create_pr.return_value = {"pr_number": 99, "pr_url": "https://x/y/pull/99"}
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, git=git_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.main_pm_complete(
        main_pm_id, root_task_id, notes="root scope reviewed and ready for production"
    )
    assert env.error is None
    assert env.status == "awaiting_ceo_approval"
    git_svc.create_pr.assert_awaited_once_with(
        "feature/backend/root123",
        parent="master",
        is_root_pr=True,
    )
    task_svc.escalate_to_ceo.assert_awaited_once()


@pytest.mark.asyncio
async def test_main_pm_complete_advances_in_progress_root_to_ceo() -> None:
    """#183: a root resumed to in_progress (subtasks all done) has no submit_up
    to reach awaiting_pm_review. main_pm_complete opens the root→master PR,
    walks the root through awaiting_pm_review, then escalates to CEO."""
    main_pm_id = uuid4()
    root_task_id = uuid4()
    in_prog = MagicMock(
        id=root_task_id,
        status="in_progress",
        assigned_to=main_pm_id,
        pr_number=None,
        branch_name="feature/main_pm/root123",
        parent_task_id=None,
        team="main_pm",
    )
    awaiting = MagicMock(**{**in_prog.__dict__, "status": "awaiting_pm_review"})
    after = MagicMock(**{**in_prog.__dict__, "status": "awaiting_ceo_approval"})
    task_svc = AsyncMock()
    task_svc.get.return_value = in_prog
    task_svc.submit_pm_review.return_value = awaiting
    task_svc.escalate_to_ceo.return_value = after
    task_svc.all_subtasks_terminal.return_value = True
    git_svc = AsyncMock()
    git_svc.create_pr.return_value = {"pr_number": 99, "pr_url": "https://x/y/pull/99"}
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, git=git_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.main_pm_complete(
        main_pm_id, root_task_id, notes="root scope reviewed; ready for CEO sign-off"
    )
    assert env.error is None
    assert env.status == "awaiting_ceo_approval"
    git_svc.create_pr.assert_awaited_once_with(
        "feature/main_pm/root123",
        parent="master",
        is_root_pr=True,
    )
    # #183: the in_progress→awaiting_pm_review hop must run before escalation.
    task_svc.submit_pm_review.assert_awaited_once()
    task_svc.escalate_to_ceo.assert_awaited_once()


@pytest.mark.asyncio
async def test_main_pm_complete_skips_pr_creation_if_already_master_targeted() -> None:
    main_pm_id = uuid4()
    root_task_id = uuid4()
    t = MagicMock(
        id=root_task_id,
        status="awaiting_pm_review",
        assigned_to=main_pm_id,
        pr_number=42,
        branch_name="feature/backend/root123",
        parent_task_id=None,
        team="backend",
    )
    after = MagicMock(**{**t.__dict__, "status": "awaiting_ceo_approval"})
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.escalate_to_ceo.return_value = after
    task_svc.all_subtasks_terminal.return_value = True
    git_svc = AsyncMock()
    git_svc.pr_target.return_value = "master"
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, git=git_svc, journal=journal_svc)
    c = Choreographer(deps)

    await c.main_pm_complete(
        main_pm_id, root_task_id, notes="root scope reviewed and ready"
    )
    git_svc.create_pr.assert_not_awaited()
    task_svc.escalate_to_ceo.assert_awaited_once()


@pytest.mark.asyncio
async def test_main_pm_complete_rejects_non_root_task() -> None:
    main_pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="awaiting_pm_review",
        assigned_to=main_pm_id,
        parent_task_id=uuid4(),  # has parent -> not a root task
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.main_pm_complete(main_pm_id, task_id, notes="x")
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "root tasks" in body["message"]


@pytest.mark.asyncio
async def test_main_pm_complete_blocks_unfinished_subtasks() -> None:
    main_pm_id = uuid4()
    root_task_id = uuid4()
    t = MagicMock(
        id=root_task_id,
        status="awaiting_pm_review",
        assigned_to=main_pm_id,
        parent_task_id=None,
        team="backend",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.all_subtasks_terminal.return_value = False
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.main_pm_complete(main_pm_id, root_task_id, notes="x")
    body = env.as_dict()
    assert body["error"] == "tracing_gap"


@pytest.mark.asyncio
async def test_complete_dispatches_cell_pm() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="awaiting_pm_review",
        assigned_to=pm_id,
        parent_task_id=uuid4(),
        pr_number=8,
        branch_name="feature/backend/abc--def",
        team="backend",
    )
    after = MagicMock(**{**t.__dict__, "status": "completed"})
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="cell_pm")
    task_svc.all_subtasks_terminal.return_value = True
    task_svc.cell_pm_complete.return_value = after
    git_svc = AsyncMock()
    git_svc.pr_merge.return_value = {"merged": True, "merge_commit_sha": "x"}
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, git=git_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.complete(
        pm_id, task_id, notes="cell scope reviewed and approved for merge"
    )
    assert env.status == "completed"
    task_svc.cell_pm_complete.assert_awaited_once()


@pytest.mark.asyncio
async def test_complete_dispatches_main_pm() -> None:
    main_pm_id = uuid4()
    root_task_id = uuid4()
    t = MagicMock(
        id=root_task_id,
        status="awaiting_pm_review",
        assigned_to=main_pm_id,
        pr_number=None,
        branch_name="feature/backend/root123",
        parent_task_id=None,
        team="backend",
    )
    after = MagicMock(**{**t.__dict__, "status": "awaiting_ceo_approval"})
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(role="main_pm")
    task_svc.all_subtasks_terminal.return_value = True
    task_svc.escalate_to_ceo.return_value = after
    git_svc = AsyncMock()
    git_svc.create_pr.return_value = {"pr_number": 99, "pr_url": "x"}
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, git=git_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.complete(
        main_pm_id, root_task_id, notes="root scope reviewed and ready"
    )
    assert env.status == "awaiting_ceo_approval"
    task_svc.escalate_to_ceo.assert_awaited_once()


@pytest.mark.asyncio
async def test_complete_rejects_non_pm_role() -> None:
    dev_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.agent_for.return_value = MagicMock(role="developer")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.complete(dev_id, task_id, notes="x")
    body = env.as_dict()
    assert body["error"] == "not_authorized"
    assert "cell_pm" in body["remediate"] and "main_pm" in body["remediate"]


@pytest.mark.asyncio
async def test_escalate_up_routes_by_escalation_target() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="blocked", assigned_to=pm_id, team="backend")
    after = MagicMock(**{**t.__dict__, "assigned_to": uuid4()})
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(
        role="cell_pm",
        escalation_target="main-pm",
    )
    task_svc.escalate.return_value = after
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.escalate_up(pm_id, task_id, reason="cross-cell coordination needed")
    assert env.error is None
    task_svc.escalate.assert_awaited_once_with(
        pm_id,
        task_id,
        "cross-cell coordination needed",
    )


@pytest.mark.asyncio
async def test_escalate_up_returns_invalid_state_when_target_lookup_fails() -> None:
    """Regression: escalate_up_to_role returning None used to crash on t.status."""
    pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="blocked", assigned_to=pm_id, team="backend")
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(
        role="cell_pm",
        escalation_target="main-pm",
    )
    task_svc.escalate.return_value = None  # target slug not found in DB
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.escalate_up(pm_id, task_id, reason="x")
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "main-pm" in body["message"]


@pytest.mark.asyncio
async def test_escalate_up_blocks_without_journal_decision() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="blocked")
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    # Spec gate runs before the journal:decision preflight; provide a
    # valid PM role so the gate passes and the preflight is the
    # load-bearing rejector.
    task_svc.agent_for.return_value = MagicMock(
        role="cell_pm", escalation_target="main-pm"
    )
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = False
    journal_svc.latest_decision_at.return_value = None
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.escalate_up(pm_id, task_id, reason="x")
    body = env.as_dict()
    assert body["error"] == "tracing_gap"
    assert "journal:decision" in body["missing"]


@pytest.mark.asyncio
async def test_escalate_up_no_target_returns_invalid_state() -> None:
    """Verb-specific preflight: PM whose escalation_target is unconfigured.

    The spec allows cell_pm/main_pm to call escalate_up regardless of
    target slug presence (target metadata lives on the agent record, not
    the lifecycle). The verb body's preflight is what surfaces the
    invalid_state when no target is configured.
    """
    pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(id=task_id, status="blocked")
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.agent_for.return_value = MagicMock(
        role="cell_pm",
        escalation_target=None,
    )
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.escalate_up(pm_id, task_id, reason="x")
    body = env.as_dict()
    assert body["error"] == "invalid_state"


@pytest.mark.asyncio
async def test_escalate_up_task_not_found() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.escalate_up(pm_id, task_id, reason="x")
    assert env.as_dict()["error"] == "not_found"
