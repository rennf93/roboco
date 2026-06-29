"""Cancel tears down the per-task worktree (F123, Phase C).

``_delete_task_branch_best_effort`` already deletes the task's REMOTE branch on
cancel. Without also removing the local per-task worktree at
``{clone_root}/.worktrees/{task-short}/``, every cancelled task leaks a full
working tree on the assignee's clone — disk blowup (plan risk #6). The reaper
(stale-claim → pending) must NOT remove it (a re-claim reuses it); only the
terminal cancel path does. The assignee is joined-eager-loaded on the task and
``_abandon_work_session_for_task`` does not clear ``assigned_to``, so the
clone root is resolvable at the cancel hook.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.models.base import Team
from roboco.services.task import TaskService


def _service() -> TaskService:
    svc = TaskService.__new__(TaskService)
    svc.log = MagicMock()
    svc.session = MagicMock()
    return svc


def _session(slug: str | None = "roboco-api") -> MagicMock:
    # Build on a local MagicMock (sub-attr assignment is allowed there) then
    # callers assign the whole thing to ``svc.session`` — assigning
    # ``svc.session.execute`` directly trips mypy's method-assign on the typed
    # AsyncSession attribute.
    session = MagicMock()
    session.execute = AsyncMock(return_value=_project_result(slug))
    return session


def _task(*, branch: str | None, assignee: MagicMock | None) -> MagicMock:
    task_id = uuid4()
    return MagicMock(
        id=task_id,
        project_id=uuid4(),
        branch_name=branch,
        assignee=assignee,
    )


def _project_result(slug: str | None) -> MagicMock:
    result = MagicMock()
    result.scalar_one_or_none.return_value = slug
    return result


@pytest.mark.asyncio
async def test_cancel_removes_worktree_for_assignee() -> None:
    svc = _service()
    task = _task(
        branch="feature/backend/abc12345",
        assignee=MagicMock(slug="be-dev-1", team=Team.BACKEND),
    )
    short = str(task.id)[:8]
    clone = Path("/data/workspaces/roboco-api/backend/be-dev-1")

    svc.session = _session("roboco-api")

    git_service = MagicMock()
    git_service.delete_task_branch = AsyncMock()
    ws_svc = MagicMock()
    ws_svc.get_clone_root_path = MagicMock(return_value=clone)
    ws_svc.remove_worktree = AsyncMock()

    with (
        patch(
            "roboco.services.git.get_git_service",
            MagicMock(return_value=git_service),
        ),
        patch(
            "roboco.services.workspace.get_workspace_service",
            MagicMock(return_value=ws_svc),
        ),
    ):
        await svc._delete_task_branch_best_effort(task)

    git_service.delete_task_branch.assert_awaited_once_with(
        "roboco-api", "feature/backend/abc12345"
    )
    ws_svc.get_clone_root_path.assert_called_once_with(
        "roboco-api", Team.BACKEND, "be-dev-1"
    )
    ws_svc.remove_worktree.assert_awaited_once()
    args = ws_svc.remove_worktree.await_args.args
    assert args[0] == clone, "remove must target the clone root"
    assert args[1] == clone / ".worktrees" / short, (
        f"remove must target the task worktree {clone}/.worktrees/{short}; "
        f"got {args[1]}"
    )


@pytest.mark.asyncio
async def test_cancel_skips_worktree_when_no_assignee() -> None:
    # Unassigned at cancel time (e.g. pooled task cancelled before any claim) —
    # no clone root to resolve, so the worktree step is skipped. The remote
    # branch is still deleted.
    svc = _service()
    task = _task(branch="feature/backend/abc12345", assignee=None)
    svc.session = _session("roboco-api")

    git_service = MagicMock()
    git_service.delete_task_branch = AsyncMock()
    ws_svc = MagicMock()
    ws_svc.remove_worktree = AsyncMock()

    with (
        patch(
            "roboco.services.git.get_git_service",
            MagicMock(return_value=git_service),
        ),
        patch(
            "roboco.services.workspace.get_workspace_service",
            MagicMock(return_value=ws_svc),
        ),
    ):
        await svc._delete_task_branch_best_effort(task)

    git_service.delete_task_branch.assert_awaited_once()
    ws_svc.remove_worktree.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancel_skips_worktree_when_no_branch() -> None:
    # Branchless coordination root — no worktree was ever created.
    svc = _service()
    task = _task(branch=None, assignee=MagicMock(slug="be-dev-1", team=Team.BACKEND))
    svc.session = _session(None)

    git_service = MagicMock()
    git_service.delete_task_branch = AsyncMock()
    ws_svc = MagicMock()
    ws_svc.remove_worktree = AsyncMock()

    with (
        patch(
            "roboco.services.git.get_git_service",
            MagicMock(return_value=git_service),
        ),
        patch(
            "roboco.services.workspace.get_workspace_service",
            MagicMock(return_value=ws_svc),
        ),
    ):
        await svc._delete_task_branch_best_effort(task)

    git_service.delete_task_branch.assert_not_awaited()
    ws_svc.remove_worktree.assert_not_awaited()
    svc.session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_worktree_cleanup_failure_does_not_raise() -> None:
    # Best-effort: a remove_worktree failure (missing clone, git error) must not
    # abort the cancel — the remote branch was already deleted.
    svc = _service()
    task = _task(
        branch="feature/backend/abc12345",
        assignee=MagicMock(slug="be-dev-1", team=Team.BACKEND),
    )
    svc.session = _session("roboco-api")

    git_service = MagicMock()
    git_service.delete_task_branch = AsyncMock()
    ws_svc = MagicMock()
    ws_svc.get_clone_root_path = MagicMock(
        return_value=Path("/data/workspaces/roboco-api/backend/be-dev-1")
    )
    ws_svc.remove_worktree = AsyncMock(side_effect=RuntimeError("boom"))

    with (
        patch(
            "roboco.services.git.get_git_service",
            MagicMock(return_value=git_service),
        ),
        patch(
            "roboco.services.workspace.get_workspace_service",
            MagicMock(return_value=ws_svc),
        ),
    ):
        await svc._delete_task_branch_best_effort(task)  # must not raise
