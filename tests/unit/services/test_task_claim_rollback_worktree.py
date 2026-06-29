"""Claim-rollback tears down the per-task worktree (F123, Phase B).

``_create_branch_in_project`` calls ``create_branch``, which cuts a worktree
at ``{clone_root}/.worktrees/{task-short}/``. If a step after the worktree-add
fails (the push, the branch_name flush), the worktree is orphaned at that path
— and a claim retry collides with the stale worktree (``git worktree add``
refuses: "already exists"). The rollback removes it (best-effort, no-op if the
worktree was never created).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.services.task import TaskService


def _service() -> TaskService:
    svc = TaskService.__new__(TaskService)
    svc.log = MagicMock()
    svc.session = MagicMock()
    return svc


@pytest.mark.asyncio
async def test_create_branch_failure_removes_worktree() -> None:
    svc = _service()
    task_id = uuid4()
    short = str(task_id)[:8]
    clone = Path("/tmp/ws")

    task = MagicMock(id=task_id, project_id=uuid4(), branch_name=None)
    project = MagicMock(slug="roboco-api")

    object.__setattr__(svc, "_resolve_parent_branch", AsyncMock(return_value=None))
    object.__setattr__(svc, "_resolve_team_dir", MagicMock(return_value="backend"))

    git_service = MagicMock()
    git_service.get_workspace = AsyncMock(return_value=clone)
    git_service.create_branch = AsyncMock(side_effect=RuntimeError("push failed"))

    ws_svc = MagicMock()
    ws_svc.remove_worktree = AsyncMock()

    with (
        patch(
            "roboco.services.git.get_git_service", MagicMock(return_value=git_service)
        ),
        patch(
            "roboco.services.workspace.get_workspace_service",
            MagicMock(return_value=ws_svc),
        ),
        pytest.raises(RuntimeError, match="push failed"),
    ):
        await svc._create_branch_in_project(task, uuid4(), project)

    ws_svc.remove_worktree.assert_awaited_once()
    args = ws_svc.remove_worktree.await_args.args
    assert args[0] == clone, "remove must target the clone root"
    assert args[1] == clone / ".worktrees" / short, (
        f"remove must target the task worktree {clone}/.worktrees/{short}; "
        f"got {args[1]}"
    )


@pytest.mark.asyncio
async def test_successful_create_does_not_remove_worktree() -> None:
    svc = _service()
    task_id = uuid4()
    clone = Path("/tmp/ws")

    task = MagicMock(id=task_id, project_id=uuid4(), branch_name=None)
    project = MagicMock(slug="roboco-api")

    object.__setattr__(svc, "_resolve_parent_branch", AsyncMock(return_value=None))
    object.__setattr__(svc, "_resolve_team_dir", MagicMock(return_value="backend"))

    git_service = MagicMock()
    git_service.get_workspace = AsyncMock(return_value=clone)
    git_service.create_branch = AsyncMock(return_value=("feature/x", "master"))

    ws_svc = MagicMock()
    ws_svc.remove_worktree = AsyncMock()

    # Assign the whole session (not svc.session.flush directly) — mypy treats
    # the typed AsyncSession.flush as a method and rejects the sub-assignment.
    session = MagicMock()
    session.flush = AsyncMock()
    svc.session = session

    with (
        patch(
            "roboco.services.git.get_git_service", MagicMock(return_value=git_service)
        ),
        patch(
            "roboco.services.workspace.get_workspace_service",
            MagicMock(return_value=ws_svc),
        ),
    ):
        out = await svc._create_branch_in_project(task, uuid4(), project)

    assert out == "feature/x"
    ws_svc.remove_worktree.assert_not_awaited()
