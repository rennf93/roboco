"""commit paths run inside the per-task worktree, not the shared clone (F123, Phase B).

``create_branch`` cuts a worktree at ``{clone_root}/.worktrees/{task-short}/``;
the agent's container cwd is pointed there at spawn. The commit paths must
follow — ``commit_for_task`` and the gateway ``commit`` resolve the worktree
from the task id, ensure it is present (re-add if pruned), and run
``git add``/``git commit`` with the worktree as cwd. A commit on the clone
root would land on whatever branch the shared checkout is parked on (the
F123 clobber, on the write side).
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest
from roboco.api.schemas.git import GitCommitRequest
from roboco.services.git import GitService


def _service() -> GitService:
    svc = GitService.__new__(GitService)
    svc.log = MagicMock()
    svc.session = MagicMock()
    return svc


def _req(task_id: UUID | None) -> GitCommitRequest:
    return GitCommitRequest(
        project_slug="roboco-api",
        task_id=task_id,
        message="implement the dashboard layout and routing",
        commit_type="feat",
        scope="panel",
        body=None,
        files=None,
    )


@pytest.mark.asyncio
async def test_commit_for_task_runs_git_in_worktree_not_clone() -> None:
    svc = _service()
    task_id = uuid4()
    short = str(task_id)[:8]
    clone = Path("/tmp/ws")
    worktree = clone / ".worktrees" / short

    task = MagicMock(branch_name="feature/backend/abc12345", id=task_id)
    object.__setattr__(
        svc, "_assert_task_owned_with_branch", AsyncMock(return_value=task)
    )
    object.__setattr__(svc, "get_workspace", AsyncMock(return_value=clone))
    object.__setattr__(svc, "_assert_on_task_branch", AsyncMock())
    object.__setattr__(svc, "_link_commit_to_task", AsyncMock())

    captured: list[Path] = []

    async def _capture_workspace(workspace: Path, *_a: object, **_k: object) -> tuple:
        captured.append(Path(workspace))
        return ("deadbeef", "msg", 1, 1, 0)

    object.__setattr__(svc, "create_commit", AsyncMock(side_effect=_capture_workspace))

    ws_svc = MagicMock()
    ws_svc.ensure_worktree_for_resume = AsyncMock()
    with patch(
        "roboco.services.git.get_workspace_service", MagicMock(return_value=ws_svc)
    ):
        await svc.commit_for_task(uuid4(), _req(task_id))

    assert captured, "create_commit must be called"
    assert captured[0] == worktree, (
        f"commit must run in the worktree {worktree}, not the clone root; "
        f"got {captured[0]}"
    )
    ws_svc.ensure_worktree_for_resume.assert_awaited_once()
    call = ws_svc.ensure_worktree_for_resume.await_args
    assert call.args[0] == clone
    assert call.args[1] == worktree
    assert call.args[2] == "feature/backend/abc12345"


@pytest.mark.asyncio
async def test_commit_for_task_without_task_id_stays_on_clone_root() -> None:
    # A no-task commit (task_id=None) has no worktree — it stays on the clone
    # root, the existing behaviour, and must NOT call ensure_worktree_for_resume.
    svc = _service()
    clone = Path("/tmp/ws")
    object.__setattr__(svc, "get_workspace", AsyncMock(return_value=clone))

    captured: list[Path] = []

    async def _capture_workspace(workspace: Path, *_a: object, **_k: object) -> tuple:
        captured.append(Path(workspace))
        return ("deadbeef", "msg", 1, 1, 0)

    object.__setattr__(svc, "create_commit", AsyncMock(side_effect=_capture_workspace))

    ws_svc = MagicMock()
    ws_svc.ensure_worktree_for_resume = AsyncMock()
    with patch(
        "roboco.services.git.get_workspace_service", MagicMock(return_value=ws_svc)
    ):
        await svc.commit_for_task(uuid4(), _req(None))

    assert captured[0] == clone
    ws_svc.ensure_worktree_for_resume.assert_not_awaited()


@pytest.mark.asyncio
async def test_gateway_commit_runs_git_in_worktree_not_clone() -> None:
    svc = _service()
    task_id = uuid4()
    short = str(task_id)[:8]
    clone = Path("/tmp/ws")
    worktree = clone / ".worktrees" / short

    object.__setattr__(svc, "_workspace_for_branch", AsyncMock(return_value=clone))
    object.__setattr__(svc, "_assert_on_task_branch", AsyncMock())
    object.__setattr__(svc, "_task_for_branch", AsyncMock(return_value=None))
    object.__setattr__(svc, "_parse_commit_stats", MagicMock(return_value=(1, 0, 1)))

    cwds: list[Path] = []

    async def _run_git(workspace: Path, args: list[str], **_kw: object) -> object:
        cwds.append(Path(workspace))
        if args[:2] == ["log", "-1"]:
            return MagicMock(stdout="deadbeef|feat: x\n", returncode=0)
        return MagicMock(stdout="", returncode=0)

    object.__setattr__(svc, "_run_git", AsyncMock(side_effect=_run_git))

    ws_svc = MagicMock()
    ws_svc.ensure_worktree_for_resume = AsyncMock()
    with patch(
        "roboco.services.git.get_workspace_service", MagicMock(return_value=ws_svc)
    ):
        out = await svc.commit(
            branch_name="feature/backend/abc12345",
            message="implement the dashboard layout and routing",
            task_id=task_id,
        )

    assert out["sha"] == "deadbeef"
    assert cwds, "git ops must run"
    assert all(c == worktree for c in cwds), (
        f"all gateway-commit git ops must run in the worktree {worktree}; got {cwds}"
    )
    ws_svc.ensure_worktree_for_resume.assert_awaited_once()
