"""The `cancel_leaf` verb — the PM's mechanical path to close a zero-diff
leaf (the 2026-09-05 de9379cc incident: a fix leaf whose findings a merged
sibling already fixed had no legitimate diff left, but `i_am_done`
(NO_COMMITS gate) and `complete` (needs an open PR) could not close it, and
the PM had no path short of a CEO cancelling it by hand).

Covers the leaf check (the target must have no children of its own,
2026-09-06 finding: `TaskService.cancel`'s cascade would otherwise
force-close a whole live subtree), ownership (cell_pm must own the
parent; main_pm may act on any root's descendant), the fail-CLOSED
zero-diff check (commits ahead, an open PR, or an unanswerable git
check all refuse), and the success path that reuses
``TaskService.cancel``.
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
    task = base["task"]
    # Every scenario below targets an actual leaf unless it overrides this.
    task.children_count.return_value = 0
    task.session.begin_nested = MagicMock(
        return_value=MagicMock(
            __aenter__=AsyncMock(return_value=None),
            __aexit__=AsyncMock(return_value=False),
        )
    )
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
    # Fresh journal:decision by default so the PM-decision gate passes;
    # tests exercising the gate boundary would stub their own value.
    base["journal"].latest_decision_at.return_value = datetime.now(UTC)
    return ChoreographerDeps(**base)


def _leaf_and_parent(
    *,
    parent_assigned_to: Any,
    pr_number: int | None = None,
    branch_name: str | None = "feature/backend/parent123--leaf456",
) -> tuple[MagicMock, MagicMock, Any, Any]:
    parent_id = uuid4()
    leaf_id = uuid4()
    parent = MagicMock(
        id=parent_id,
        assigned_to=parent_assigned_to,
        branch_name="feature/backend/parent123",
    )
    leaf = MagicMock(
        id=leaf_id,
        parent_task_id=parent_id,
        pr_number=pr_number,
        branch_name=branch_name,
        status="pending",
    )
    return leaf, parent, leaf_id, parent_id


def _wire_task_get(task_svc: AsyncMock, leaf: MagicMock, parent: MagicMock) -> None:
    task_svc.get.side_effect = lambda tid: parent if tid == parent.id else leaf


@pytest.mark.asyncio
async def test_owner_cell_pm_zero_diff_leaf_succeeds() -> None:
    pm_id = uuid4()
    leaf, parent, leaf_id, _parent_id = _leaf_and_parent(parent_assigned_to=pm_id)
    task_svc = AsyncMock()
    _wire_task_get(task_svc, leaf, parent)
    task_svc.agent_for.return_value = MagicMock(
        id=pm_id, role="cell_pm", team="backend", slug="be-pm"
    )
    cancelled = MagicMock(status="cancelled", id=leaf_id, parent_task_id=parent.id)
    task_svc.cancel.return_value = cancelled
    git_svc = AsyncMock()
    git_svc.is_behind_base.return_value = (0, 0)
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    env = await c.cancel_leaf(pm_id, leaf_id, "sibling PR #12 already fixed this")

    assert env.error is None
    task_svc.cancel.assert_awaited_once()
    call = task_svc.cancel.await_args
    assert call.args[0] == leaf_id
    assert call.kwargs["agent_role"] == "cell_pm"
    assert "sibling PR #12 already fixed this" in call.kwargs["cancellation_note"]


@pytest.mark.asyncio
async def test_no_branch_at_all_is_trivially_zero_diff() -> None:
    """A leaf never claimed/started has no branch — nothing to lose."""
    pm_id = uuid4()
    leaf, parent, leaf_id, _parent_id = _leaf_and_parent(
        parent_assigned_to=pm_id, branch_name=None
    )
    task_svc = AsyncMock()
    _wire_task_get(task_svc, leaf, parent)
    task_svc.agent_for.return_value = MagicMock(
        id=pm_id, role="cell_pm", team="backend", slug="be-pm"
    )
    task_svc.cancel.return_value = MagicMock(status="cancelled", id=leaf_id)
    git_svc = AsyncMock()
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    env = await c.cancel_leaf(pm_id, leaf_id, "never started; duplicate of #99")

    assert env.error is None
    git_svc.is_behind_base.assert_not_awaited()
    task_svc.cancel.assert_awaited_once()


@pytest.mark.asyncio
async def test_main_pm_may_act_on_any_roots_descendant() -> None:
    """main_pm needs no direct assignment to the parent — it coordinates
    across every cell."""
    main_pm_id = uuid4()
    other_pm_id = uuid4()
    leaf, parent, leaf_id, _parent_id = _leaf_and_parent(parent_assigned_to=other_pm_id)
    task_svc = AsyncMock()
    _wire_task_get(task_svc, leaf, parent)
    task_svc.agent_for.return_value = MagicMock(
        id=main_pm_id, role="main_pm", team=None, slug="main-pm"
    )
    task_svc.cancel.return_value = MagicMock(status="cancelled", id=leaf_id)
    git_svc = AsyncMock()
    git_svc.is_behind_base.return_value = (0, 0)
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    env = await c.cancel_leaf(
        main_pm_id, leaf_id, "duplicate fix; sibling merged first"
    )

    assert env.error is None
    task_svc.cancel.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_owner_cell_pm_refused() -> None:
    pm_id = uuid4()
    other_pm_id = uuid4()
    leaf, parent, leaf_id, _parent_id = _leaf_and_parent(parent_assigned_to=other_pm_id)
    task_svc = AsyncMock()
    _wire_task_get(task_svc, leaf, parent)
    task_svc.agent_for.return_value = MagicMock(
        id=pm_id, role="cell_pm", team="backend", slug="be-pm"
    )
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.cancel_leaf(pm_id, leaf_id, "not my task's parent")

    assert env.error == "not_authorized"
    task_svc.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_target_with_children_refused() -> None:
    """2026-09-06 finding: `cancel_leaf` never checked whether the target
    itself had descendants before reusing `TaskService.cancel`, whose
    cascade force-cancels every non-terminal child. A target with even
    one child is not a leaf, refuse before ownership/zero-diff run at
    all, and never touch `TaskService.cancel`."""
    pm_id = uuid4()
    leaf, parent, leaf_id, _parent_id = _leaf_and_parent(parent_assigned_to=pm_id)
    task_svc = AsyncMock()
    _wire_task_get(task_svc, leaf, parent)
    task_svc.agent_for.return_value = MagicMock(
        id=pm_id, role="cell_pm", team="backend", slug="be-pm"
    )
    git_svc = AsyncMock()
    deps = _make_deps(task=task_svc, git=git_svc)
    # _make_deps stubs children_count=0 (every other scenario is a real
    # leaf); override after, not before, or the default clobbers this.
    task_svc.children_count.return_value = 1
    c = Choreographer(deps)

    env = await c.cancel_leaf(pm_id, leaf_id, "not actually a leaf")

    assert env.error == "invalid_state"
    assert "has 1 children" in (env.message or "")
    task_svc.children_count.assert_awaited_with(leaf_id)
    git_svc.is_behind_base.assert_not_awaited()
    task_svc.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_root_task_without_parent_refused() -> None:
    pm_id = uuid4()
    task_id = uuid4()
    root = MagicMock(id=task_id, parent_task_id=None, status="in_progress")
    task_svc = AsyncMock()
    task_svc.get.return_value = root
    task_svc.agent_for.return_value = MagicMock(
        id=pm_id, role="main_pm", team=None, slug="main-pm"
    )
    deps = _make_deps(task=task_svc)
    c = Choreographer(deps)

    env = await c.cancel_leaf(pm_id, task_id, "this is a root, not a leaf")

    assert env.error == "invalid_state"
    task_svc.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_leaf_with_open_pr_refused() -> None:
    pm_id = uuid4()
    leaf, parent, leaf_id, _parent_id = _leaf_and_parent(
        parent_assigned_to=pm_id, pr_number=42
    )
    task_svc = AsyncMock()
    _wire_task_get(task_svc, leaf, parent)
    task_svc.agent_for.return_value = MagicMock(
        id=pm_id, role="cell_pm", team="backend", slug="be-pm"
    )
    git_svc = AsyncMock()
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    env = await c.cancel_leaf(pm_id, leaf_id, "there is an open PR here")

    assert env.error == "invalid_state"
    assert "open PR" in (env.message or "")
    git_svc.is_behind_base.assert_not_awaited()
    task_svc.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_leaf_with_commits_ahead_refused() -> None:
    pm_id = uuid4()
    leaf, parent, leaf_id, _parent_id = _leaf_and_parent(parent_assigned_to=pm_id)
    task_svc = AsyncMock()
    _wire_task_get(task_svc, leaf, parent)
    task_svc.agent_for.return_value = MagicMock(
        id=pm_id, role="cell_pm", team="backend", slug="be-pm"
    )
    git_svc = AsyncMock()
    git_svc.is_behind_base.return_value = (0, 2)  # 2 commits ahead — real diff
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    env = await c.cancel_leaf(pm_id, leaf_id, "not actually zero-diff")

    assert env.error == "invalid_state"
    assert "not a zero-diff leaf" in (env.message or "")
    task_svc.cancel.assert_not_awaited()


@pytest.mark.asyncio
async def test_git_failure_refuses_fail_closed() -> None:
    """An unanswerable git check must never read as 'safe to cancel' — the
    opposite of _maybe_waive_pr_creation's fail-open (that path only ever
    skips a PR-creation side effect; this one authorizes discarding a
    task)."""
    pm_id = uuid4()
    leaf, parent, leaf_id, _parent_id = _leaf_and_parent(parent_assigned_to=pm_id)
    task_svc = AsyncMock()
    _wire_task_get(task_svc, leaf, parent)
    task_svc.agent_for.return_value = MagicMock(
        id=pm_id, role="cell_pm", team="backend", slug="be-pm"
    )
    git_svc = AsyncMock()
    git_svc.is_behind_base.side_effect = RuntimeError("workspace unreachable")
    deps = _make_deps(task=task_svc, git=git_svc)
    c = Choreographer(deps)

    env = await c.cancel_leaf(pm_id, leaf_id, "should refuse, not guess")

    assert env.error == "invalid_state"
    assert "could not verify" in (env.message or "")
    task_svc.cancel.assert_not_awaited()
