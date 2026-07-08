"""Tests for PM Choreographer methods.

Covers: triage, triage_all, unblock, complete, escalate_up.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
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

    env = await c.unblock(
        pm_id, task_id, "block resolved upstream; restoring", restore=True
    )
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
    env = await c.unblock(pm_id, task_id, "block resolved upstream; restoring")
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

    env = await c.unblock(pm_id, task_id, "block resolved upstream; restoring")
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

    env = await c.unblock(pm_id, task_id, "block resolved upstream; restoring")
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

    env = await c.unblock(
        pm_id, task_id, "block resolved upstream; restoring", restore=False
    )
    body = env.as_dict()
    assert body["status"] == "in_progress"
    assert "re-engage" in body["next"].lower()


@pytest.mark.asyncio
async def test_unblock_refused_while_a_dependency_is_unfinished() -> None:
    """A dependency block can't be force-cleared by a PM.

    It auto-clears via _unblock_dependents once the upstream completes; manual
    unblock would let the dependent proceed without the upstream's work.
    """
    pm_id = uuid4()
    task_id = uuid4()
    dep_id = uuid4()
    t = MagicMock(id=task_id, status="blocked", dependency_ids=[dep_id])
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.unmet_dependency_ids.return_value = [dep_id]
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    deps = _make_deps(task=task_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.unblock(pm_id, task_id, "block resolved upstream; restoring")
    body = env.as_dict()

    assert body["error"] == "invalid_state"
    assert "depends on" in body["message"]
    # The task must not have been advanced out of blocked.
    task_svc.unblock_with_restore.assert_not_awaited()


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
    git_svc.is_pr_merged_for_task.return_value = False
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
        8,
        target="feature/main_pm/abc",
        project_id=t.project_id,
        actor_agent_id=pm_id,
    )


@pytest.mark.asyncio
async def test_cell_pm_complete_does_not_500_when_complete_returns_none() -> None:
    """`complete()` returns None when its PR-merged guard fails — which is
    exactly what happened live when the merge recorded against the WRONG
    task's work session (the cross-repo pr_number collision) left this
    task's `pr_status="open"`. `_finalize_cell_complete` used to deref
    `t.status` on the None and 500, leaving the cell PM thrashing
    escalate<->blocked until the CEO intervened. It must fail closed into a
    clean invalid_state envelope, never a 500.
    """
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
    parent = MagicMock(
        id=parent_id, branch_name="feature/main_pm/abc", parent_task_id=None
    )
    task_svc = AsyncMock()
    task_svc.get.side_effect = lambda tid: parent if tid == parent_id else t
    task_svc.all_subtasks_terminal.return_value = True
    task_svc.cell_pm_complete.return_value = None  # complete() rejected
    git_svc = AsyncMock()
    git_svc.is_pr_merged_for_task.return_value = False
    git_svc.pr_merge.return_value = {"merge_commit_sha": "merge-abc"}
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, git=git_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.cell_pm_complete(pm_id, task_id, notes="reviewed and approved")
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert (
        "merged" in body.get("message", "").lower()
        or "complete" in body.get("message", "").lower()
    )
    # No AttributeError 500 — the verb completed cleanly with a remediation.
    assert body.get("remediate")


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
async def test_cell_pm_complete_in_progress_steers_to_submit_up() -> None:
    """Mirror of the main-PM submit_root steer: a cell task still in_progress
    must enter the gate via submit_up first. The rejection must NAME submit_up
    so the cell PM isn't left guessing the verb (the gap that deadlocked the
    main PM on submit_root)."""
    pm_id = uuid4()
    task_id = uuid4()
    t = MagicMock(
        id=task_id,
        status="in_progress",
        assigned_to=pm_id,
        parent_task_id=uuid4(),
        branch_name="feature/backend/parent123",
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
        pm_id, task_id, notes="cell scope assembled; ready to bubble up"
    )
    assert env.error is not None
    assert "submit_up" in (env.remediate or "")


@pytest.mark.asyncio
async def test_main_pm_complete_escalates_code_root_without_reopening_pr() -> None:
    """A code root reaches main_pm_complete already in awaiting_pm_review —
    submit_root opened the root→master PR and the main reviewer pr_passed it —
    so complete just escalates to the CEO and does NOT reopen the PR."""
    main_pm_id = uuid4()
    root_task_id = uuid4()
    t = MagicMock(
        id=root_task_id,
        status="awaiting_pm_review",
        assigned_to=main_pm_id,
        pr_number=99,
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
    git_svc.create_pr.assert_not_awaited()
    task_svc.escalate_to_ceo.assert_awaited_once()


@pytest.mark.asyncio
async def test_main_pm_complete_rejects_in_progress_code_root_toward_submit_root() -> (
    None
):
    """A code root must pass the in-path gate first. main_pm_complete rejects it
    while still in_progress and points the Main PM at submit_root."""
    main_pm_id = uuid4()
    root_task_id = uuid4()
    t = MagicMock(
        id=root_task_id,
        status="in_progress",
        assigned_to=main_pm_id,
        pr_number=None,
        branch_name="feature/main_pm/root123",
        parent_task_id=None,
        team="main_pm",
    )
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.all_subtasks_terminal.return_value = True
    git_svc = AsyncMock()
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, git=git_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.main_pm_complete(
        main_pm_id, root_task_id, notes="root scope reviewed; ready for CEO sign-off"
    )
    assert env.error is not None
    assert "submit_root" in (env.remediate or "")
    task_svc.escalate_to_ceo.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_root_accepts_main_pm_and_enters_the_gate() -> None:
    """submit_root is the Main PM's entry to the in-path gate. It reuses the
    cell PM's _submit_up_guard for ownership/state, so the ownership guard must
    NOT hardcode-reject main_pm — otherwise submit_root and complete point at
    each other and a code root can never close (the circular-rejection bug)."""
    main_pm_id = uuid4()
    root_task_id = uuid4()
    in_prog = MagicMock(
        id=root_task_id,
        status="in_progress",
        assigned_to=main_pm_id,
        pr_number=None,
        branch_name="feature/main_pm/root123",
        parent_task_id=None,
        batch_id=None,  # a normal root carries no batch_id (not a MegaTask umbrella)
        team="main_pm",
    )
    gated = MagicMock(**{**in_prog.__dict__, "status": "awaiting_pr_review"})
    task_svc = AsyncMock()
    task_svc.get.return_value = in_prog
    task_svc.submit_for_review.return_value = gated
    task_svc.all_subtasks_terminal.return_value = True
    task_svc.uncovered_parent_acceptance_criteria.return_value = []
    task_svc.agent_for.return_value = MagicMock(role="main_pm", team="main_pm")
    task_svc.session.begin_nested = MagicMock(
        return_value=MagicMock(__aenter__=AsyncMock(), __aexit__=AsyncMock())
    )
    git_svc = AsyncMock()
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, git=git_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.submit_root(
        main_pm_id, root_task_id, notes="root scope assembled; opening root→master PR"
    )
    assert env.error is None, env.as_dict()
    assert env.status == "awaiting_pr_review"
    task_svc.submit_for_review.assert_awaited_once()


@pytest.mark.asyncio
async def test_main_pm_complete_walks_branchless_coordination_root_to_ceo() -> None:
    """A branchless coordination root (product fan-out, no repo/PR) skips the
    in-path gate: main_pm_complete walks it in_progress→awaiting_pm_review and
    escalates to the CEO. No root→master PR is created (it has no branch)."""
    main_pm_id = uuid4()
    root_task_id = uuid4()
    in_prog = MagicMock(
        id=root_task_id,
        status="in_progress",
        assigned_to=main_pm_id,
        pr_number=None,
        branch_name=None,
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
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, git=git_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.main_pm_complete(
        main_pm_id, root_task_id, notes="coordination root reviewed; ready for CEO"
    )
    assert env.error is None
    assert env.status == "awaiting_ceo_approval"
    git_svc.create_pr.assert_not_awaited()
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
        batch_id=None,  # a plain subtask, NOT a MegaTask root-subtask
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
async def test_main_pm_complete_allows_batch_root_subtask() -> None:
    """A MegaTask root-subtask IS parented (the umbrella) yet carries its own
    project/branch/PR and behaves as a root for git/CEO purposes — the
    parent_task_id refusal above must NOT fire for it (is_batch_root_subtask)."""
    main_pm_id = uuid4()
    root_task_id = uuid4()
    t = MagicMock(
        id=root_task_id,
        status="awaiting_pm_review",
        assigned_to=main_pm_id,
        pr_number=42,
        branch_name="feature/main_pm/rootsub1",
        parent_task_id=uuid4(),  # the umbrella
        batch_id=uuid4(),  # batch_id + parent_task_id -> is_batch_root_subtask
        team="main_pm",
    )
    after = MagicMock(**{**t.__dict__, "status": "awaiting_ceo_approval"})
    task_svc = AsyncMock()
    task_svc.get.return_value = t
    task_svc.escalate_to_ceo.return_value = after
    task_svc.all_subtasks_terminal.return_value = True
    task_svc.uncovered_parent_acceptance_criteria.return_value = []
    git_svc = AsyncMock()
    git_svc.pr_target.return_value = "master"
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, git=git_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.main_pm_complete(
        main_pm_id, root_task_id, notes="root-subtask reviewed and ready"
    )
    assert env.error is None, env.as_dict()
    assert env.status == "awaiting_ceo_approval"
    task_svc.escalate_to_ceo.assert_awaited_once()


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
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
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

    env = await c.complete(dev_id, task_id, notes="reviewed and approved")
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

    env = await c.escalate_up(pm_id, task_id, reason="needs cross-cell coordination")
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

    env = await c.escalate_up(pm_id, task_id, reason="needs cross-cell coordination")
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

    env = await c.escalate_up(pm_id, task_id, reason="needs cross-cell coordination")
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

    env = await c.escalate_up(pm_id, task_id, reason="needs cross-cell coordination")
    assert env.as_dict()["error"] == "not_found"


# ---------------------------------------------------------------------------
# H7: cell_pm_complete idempotent when PR already merged to target
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cell_pm_complete_idempotent_when_pr_already_merged_to_target() -> None:
    """Re-issuing cell_pm_complete after the PR is already merged to the
    target must not call git.pr_merge again (which would 405) and must not
    500. It should complete the task or surface invalid_state cleanly."""
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
    parent = MagicMock(
        id=parent_id, branch_name="feature/main_pm/abc", parent_task_id=None
    )
    task_svc = AsyncMock()
    task_svc.get.side_effect = lambda tid: parent if tid == parent_id else t
    task_svc.all_subtasks_terminal.return_value = True
    task_svc.cell_pm_complete.return_value = after
    git_svc = AsyncMock()
    git_svc.is_pr_merged_for_task.return_value = True  # already merged
    git_svc.pr_merge.return_value = {"merge_commit_sha": "merge-abc"}
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, git=git_svc, journal=journal_svc)
    c = Choreographer(deps)

    env = await c.cell_pm_complete(pm_id, task_id, notes="re-issue after None complete")
    body = env.as_dict()
    assert body.get("error") is None or body.get("error") == "invalid_state", body
    assert not git_svc.pr_merge.called, (
        "cell_pm_complete re-issued git.pr_merge on an already-merged PR"
    )


# ---------------------------------------------------------------------------
# H6: cell_pm_complete survives parent-advance failure
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_cell_pm_complete_survives_parent_advance_failure() -> None:
    """_maybe_advance_parent_to_pm_review throws after the leaf is completed.
    The verb must NOT 500 — the completion is committed; the side-effect
    failure is logged and the envelope carries a warning."""
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
    parent = MagicMock(
        id=parent_id, branch_name="feature/main_pm/abc", parent_task_id=None
    )
    task_svc = AsyncMock()
    task_svc.get.side_effect = lambda tid: parent if tid == parent_id else t
    task_svc.all_subtasks_terminal.return_value = True
    task_svc.cell_pm_complete.return_value = after
    git_svc = AsyncMock()
    git_svc.is_pr_merged_for_task.return_value = False
    git_svc.pr_merge.return_value = {"merge_commit_sha": "merge-abc"}
    journal_svc = AsyncMock()
    journal_svc.has_decision_for_task.return_value = True
    journal_svc.latest_decision_at.return_value = datetime.now(UTC)
    journal_svc.has_reflect_for_task.return_value = True
    deps = _make_deps(task=task_svc, git=git_svc, journal=journal_svc)
    c = Choreographer(deps)

    with patch.object(
        c,
        "_maybe_advance_parent_to_pm_review",
        new=AsyncMock(side_effect=RuntimeError("advance down")),
    ):
        env = await c.cell_pm_complete(pm_id, task_id, notes="reviewed and approved")
    body = env.as_dict()
    assert body.get("error") is None, body
    assert body.get("warning") is not None
    assert "advance" in body["warning"].lower()


# ---------------------------------------------------------------------------
# declare_coverage
# ---------------------------------------------------------------------------


def _declare_coverage_deps(
    *,
    parent_id: Any,
    child_id: Any,
    parent_kwargs: dict[str, Any],
    child_kwargs: dict[str, Any],
    agent_kwargs: dict[str, Any],
) -> tuple[AsyncMock, MagicMock, MagicMock]:
    """Wire a task_svc AsyncMock whose .get resolves parent_id/child_id, plus
    the parent + child MagicMocks (declare_coverage loads both by id)."""
    parent = MagicMock(id=parent_id, **parent_kwargs)
    child = MagicMock(id=child_id, parent_task_id=parent_id, **child_kwargs)
    task_svc = AsyncMock()
    task_svc.get.side_effect = lambda tid: parent if tid == parent_id else child
    task_svc.agent_for.return_value = MagicMock(**agent_kwargs)
    return task_svc, parent, child


@pytest.mark.asyncio
async def test_declare_coverage_task_not_found() -> None:
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.declare_coverage(uuid4(), uuid4(), ["id-a"])
    assert env.error == "not_found"


@pytest.mark.asyncio
async def test_declare_coverage_no_parent_returns_invalid_state() -> None:
    pm_id = uuid4()
    child_id = uuid4()
    child = MagicMock(id=child_id, parent_task_id=None, team="backend")
    task_svc = AsyncMock()
    task_svc.get.return_value = child
    task_svc.agent_for.return_value = MagicMock(role="cell_pm", team="backend")
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.declare_coverage(pm_id, child_id, ["id-a"])
    assert env.error == "invalid_state"
    task_svc.add_parent_ac_refs.assert_not_awaited()


@pytest.mark.asyncio
async def test_declare_coverage_non_pm_rejected() -> None:
    pm_id = uuid4()
    parent_id, child_id = uuid4(), uuid4()
    task_svc, _parent, _child = _declare_coverage_deps(
        parent_id=parent_id,
        child_id=child_id,
        parent_kwargs={"assigned_to": pm_id},
        child_kwargs={"team": "backend"},
        agent_kwargs={"role": "developer", "team": "backend"},
    )
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.declare_coverage(pm_id, child_id, ["id-a"])
    assert env.error == "not_authorized"
    task_svc.add_parent_ac_refs.assert_not_awaited()


@pytest.mark.asyncio
async def test_declare_coverage_rejects_pm_off_team_without_parent_ownership() -> None:
    pm_id, other_pm_id = uuid4(), uuid4()
    parent_id, child_id = uuid4(), uuid4()
    task_svc, _parent, _child = _declare_coverage_deps(
        parent_id=parent_id,
        child_id=child_id,
        parent_kwargs={"assigned_to": other_pm_id},
        child_kwargs={"team": "frontend"},
        agent_kwargs={"role": "cell_pm", "team": "backend"},
    )
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.declare_coverage(pm_id, child_id, ["id-a"])
    assert env.error == "not_authorized"


@pytest.mark.asyncio
async def test_declare_coverage_allows_pm_on_child_team_without_ownership() -> None:
    """The minimum authorization bar: a PM on the child's own team may
    declare coverage even without owning the parent coordination task."""
    pm_id, other_pm_id = uuid4(), uuid4()
    parent_id, child_id = uuid4(), uuid4()
    task_svc, _parent, child = _declare_coverage_deps(
        parent_id=parent_id,
        child_id=child_id,
        parent_kwargs={
            "assigned_to": other_pm_id,
            "acceptance_criteria": ["crit a"],
            "acceptance_criteria_ids": ["id-a"],
        },
        child_kwargs={"team": "backend", "status": "completed"},
        agent_kwargs={"role": "cell_pm", "team": "backend"},
    )
    task_svc.unknown_ac_refs = MagicMock(return_value=[])
    task_svc.add_parent_ac_refs.return_value = child
    task_svc.uncovered_parent_acceptance_criteria.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.declare_coverage(pm_id, child_id, ["id-a"])
    assert env.error is None, env.as_dict()


@pytest.mark.asyncio
async def test_declare_coverage_unknown_criterion_rejected_lists_parent_acs() -> None:
    pm_id = uuid4()
    parent_id, child_id = uuid4(), uuid4()
    task_svc, _parent, _child = _declare_coverage_deps(
        parent_id=parent_id,
        child_id=child_id,
        parent_kwargs={
            "assigned_to": pm_id,
            "acceptance_criteria": ["crit a", "crit b"],
            "acceptance_criteria_ids": ["id-a", "id-b"],
        },
        child_kwargs={"team": "backend"},
        agent_kwargs={"role": "cell_pm", "team": "backend"},
    )
    task_svc.unknown_ac_refs = MagicMock(return_value=["bogus"])
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.declare_coverage(pm_id, child_id, ["bogus"])
    assert env.error == "invalid_state"
    assert env.remediate is not None
    assert "crit a" in env.remediate and "crit b" in env.remediate
    task_svc.add_parent_ac_refs.assert_not_awaited()


@pytest.mark.asyncio
async def test_declare_coverage_happy_path_stamps_refs_and_returns_remaining() -> None:
    pm_id = uuid4()
    parent_id, child_id = uuid4(), uuid4()
    task_svc, _parent, child = _declare_coverage_deps(
        parent_id=parent_id,
        child_id=child_id,
        parent_kwargs={
            "assigned_to": pm_id,
            "acceptance_criteria": ["crit a", "crit b"],
            "acceptance_criteria_ids": ["id-a", "id-b"],
        },
        child_kwargs={"team": "backend", "status": "completed"},
        agent_kwargs={"role": "cell_pm", "team": "backend"},
    )
    task_svc.unknown_ac_refs = MagicMock(return_value=[])
    task_svc.add_parent_ac_refs.return_value = child
    task_svc.uncovered_parent_acceptance_criteria.return_value = ["crit b"]
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.declare_coverage(pm_id, child_id, ["id-a"])
    assert env.error is None, env.as_dict()
    task_svc.add_parent_ac_refs.assert_awaited_once_with(
        child_id, ["id-a"], declared_by=pm_id
    )
    assert env.evidence == {"remaining_uncovered_parent_acs": ["crit b"]}


@pytest.mark.asyncio
async def test_declare_coverage_idempotent_redeclare() -> None:
    pm_id = uuid4()
    parent_id, child_id = uuid4(), uuid4()
    task_svc, _parent, child = _declare_coverage_deps(
        parent_id=parent_id,
        child_id=child_id,
        parent_kwargs={
            "assigned_to": pm_id,
            "acceptance_criteria": ["crit a"],
            "acceptance_criteria_ids": ["id-a"],
        },
        child_kwargs={"team": "backend", "status": "completed"},
        agent_kwargs={"role": "cell_pm", "team": "backend"},
    )
    task_svc.unknown_ac_refs = MagicMock(return_value=[])
    task_svc.add_parent_ac_refs.return_value = child
    task_svc.uncovered_parent_acceptance_criteria.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    first = await c.declare_coverage(pm_id, child_id, ["id-a"])
    count_after_first = task_svc.add_parent_ac_refs.await_count
    second = await c.declare_coverage(pm_id, child_id, ["id-a"])

    assert first.error is None, first.as_dict()
    assert second.error is None, second.as_dict()
    assert task_svc.add_parent_ac_refs.await_count == count_after_first + 1


@pytest.mark.asyncio
async def test_declare_coverage_then_submit_up_gate_passes() -> None:
    """declare_coverage followed by the roll-up gate — the production
    deadlock's end-to-end fix: once uncovered_parent_acceptance_criteria
    empties, _parent_acs_covered_envelope no longer blocks submit_up."""
    pm_id = uuid4()
    parent_id, child_id = uuid4(), uuid4()
    task_svc, _parent, child = _declare_coverage_deps(
        parent_id=parent_id,
        child_id=child_id,
        parent_kwargs={
            "assigned_to": pm_id,
            "acceptance_criteria": ["crit a"],
            "acceptance_criteria_ids": ["id-a"],
        },
        child_kwargs={"team": "backend", "status": "completed"},
        agent_kwargs={"role": "cell_pm", "team": "backend"},
    )
    task_svc.unknown_ac_refs = MagicMock(return_value=[])
    task_svc.add_parent_ac_refs.return_value = child
    task_svc.uncovered_parent_acceptance_criteria.return_value = []
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.declare_coverage(pm_id, child_id, ["id-a"])
    assert env.error is None, env.as_dict()
    assert env.evidence == {"remaining_uncovered_parent_acs": []}

    gate_env = await c._parent_acs_covered_envelope(
        pm_id, parent_id, context_phrase="bubbling up"
    )
    assert gate_env is None
