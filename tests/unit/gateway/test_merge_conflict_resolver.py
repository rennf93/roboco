"""Conflict resolver: a leaf PR that can't merge is resolved, never looped.

When a sibling lands overlapping work first, ``cell_pm_complete``'s merge
raises ``MergeConflictError``. The resolver rebases and acts on the outcome:
re-merge (rebased), close + complete (superseded), or escalate to the CEO
(genuine conflicts) — instead of failing back into a respawn loop.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.exceptions import MergeConflictError
from roboco.models.base import TaskStatus
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_deps(**overrides: AsyncMock) -> ChoreographerDeps:
    task = overrides.get("task", AsyncMock())
    git = overrides.get("git", AsyncMock())
    evidence_repo = overrides.get("evidence_repo", AsyncMock())
    return ChoreographerDeps(
        task=task,
        work_session=overrides.get("work_session", AsyncMock()),
        git=git,
        a2a=overrides.get("a2a", AsyncMock()),
        journal=overrides.get("journal", AsyncMock()),
        audit=overrides.get("audit", AsyncMock()),
        evidence_repo=evidence_repo,
    )


def _choreo(
    task: AsyncMock, git: AsyncMock, monkeypatch: pytest.MonkeyPatch
) -> Choreographer:
    choreo = Choreographer(_make_deps(task=task, git=git))
    # Isolate from briefing assembly (hits many repos) and CEO notification.
    # monkeypatch.setattr (not direct assignment) keeps mypy's method-assign
    # check satisfied without silencing it.
    monkeypatch.setattr(choreo, "_briefing_for", AsyncMock(return_value={}))
    monkeypatch.setattr(choreo, "_notify_ceo_merge_conflict", AsyncMock())
    monkeypatch.setattr(choreo, "_maybe_advance_parent_to_pm_review", AsyncMock())
    return choreo


_EXC = MergeConflictError("GitHub API refused PR merge (405): not mergeable")


@pytest.mark.asyncio
async def test_superseded_closes_pr_and_completes_without_merge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All work already in base -> close the dead PR + complete, no re-merge."""
    git = AsyncMock()
    git.rebase_pr_for_task = AsyncMock(return_value={"status": "superseded"})
    git.close_pull_request = AsyncMock()
    git.pr_merge = AsyncMock()
    task = AsyncMock()
    task.cell_pm_complete = AsyncMock(
        return_value=MagicMock(status="completed", parent_task_id=None, team="frontend")
    )
    choreo = _choreo(task, git, monkeypatch)
    t = MagicMock(pr_number=159, parent_task_id=None, team="frontend")

    env = await choreo._resolve_merge_conflict_on_complete(
        uuid4(), uuid4(), t, "feature/frontend/root--cell", "notes", _EXC
    )

    git.close_pull_request.assert_awaited_once()
    task.cell_pm_complete.assert_awaited_once()
    # No redundant merge for a superseded branch.
    git.pr_merge.assert_not_awaited()
    # Completed without a merge commit.
    assert task.cell_pm_complete.await_args.kwargs["merge_commit"] is None
    assert env.error is None


@pytest.mark.asyncio
async def test_rebased_retries_merge_and_completes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clean rebase with unique work -> retry the merge, then complete."""
    git = AsyncMock()
    git.rebase_pr_for_task = AsyncMock(
        return_value={"status": "rebased", "unique_commits": 2}
    )
    git.pr_merge = AsyncMock(return_value={"merge_commit_sha": "abc123"})
    git.close_pull_request = AsyncMock()
    task = AsyncMock()
    task.cell_pm_complete = AsyncMock(
        return_value=MagicMock(status="completed", parent_task_id=None, team="frontend")
    )
    choreo = _choreo(task, git, monkeypatch)
    t = MagicMock(pr_number=160, parent_task_id=None, team="backend")

    await choreo._resolve_merge_conflict_on_complete(
        uuid4(), uuid4(), t, "feature/backend/root--cell", "notes", _EXC
    )

    git.pr_merge.assert_awaited_once()
    git.close_pull_request.assert_not_awaited()
    assert task.cell_pm_complete.await_args.kwargs["merge_commit"] == "abc123"


@pytest.mark.asyncio
async def test_genuine_conflict_escalates_to_ceo_and_does_not_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unresolvable conflict -> admin override to awaiting_ceo_approval."""
    git = AsyncMock()
    git.rebase_pr_for_task = AsyncMock(
        return_value={"status": "conflicts", "files": ["src/a.py", "src/b.py"]}
    )
    git.close_pull_request = AsyncMock()
    git.pr_merge = AsyncMock()
    task = AsyncMock()
    task.admin_set_status = AsyncMock()
    task.get = AsyncMock(return_value=MagicMock(status="awaiting_ceo_approval"))
    choreo = _choreo(task, git, monkeypatch)
    # Hold a local ref to the notification mock so the assertion has a typed
    # AsyncMock to call (the attribute itself reads as the original method type).
    notify = AsyncMock()
    monkeypatch.setattr(choreo, "_notify_ceo_merge_conflict", notify)
    tid = uuid4()
    t = MagicMock(pr_number=160, parent_task_id=None, team="backend")

    env = await choreo._resolve_merge_conflict_on_complete(
        uuid4(), tid, t, "feature/backend/root--cell", "notes", _EXC
    )

    task.admin_set_status.assert_awaited_once()
    args = task.admin_set_status.await_args.args
    assert args[0] == tid
    assert args[1] == TaskStatus.AWAITING_CEO_APPROVAL
    # Never closes or re-merges a branch with real unresolved conflicts.
    git.close_pull_request.assert_not_awaited()
    git.pr_merge.assert_not_awaited()
    task.cell_pm_complete.assert_not_awaited()
    notify.assert_awaited_once()
    assert env.error is None


@pytest.mark.asyncio
async def test_unknown_rebase_outcome_escalates_rather_than_completing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-classifiable rebase result must escalate, not silently complete."""
    git = AsyncMock()
    git.rebase_pr_for_task = AsyncMock(return_value={"status": "unknown"})
    git.close_pull_request = AsyncMock()
    git.pr_merge = AsyncMock()
    task = AsyncMock()
    task.admin_set_status = AsyncMock()
    task.get = AsyncMock(return_value=MagicMock(status="awaiting_ceo_approval"))
    choreo = _choreo(task, git, monkeypatch)
    t = MagicMock(pr_number=160, parent_task_id=None, team="backend")

    await choreo._resolve_merge_conflict_on_complete(
        uuid4(), uuid4(), t, "feature/backend/root--cell", "notes", _EXC
    )

    task.admin_set_status.assert_awaited_once()
    task.cell_pm_complete.assert_not_awaited()
    git.close_pull_request.assert_not_awaited()
