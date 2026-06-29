"""Rebase + conventions validator run in the per-task worktree, not the clone.

F123 gap: Phase B routed ``create_branch`` + ``commit`` to the worktree but
missed two cwd-dependent git ops that still resolved the clone root:

1. ``rebase_onto_base`` (called by ``sync_task_branch`` + ``rebase_pr_for_task``)
   does ``git checkout <head>`` + ``git reset --hard origin/<head>`` in the
   resolved workspace. Post-F123 the branch is checked out in the linked
   worktree, so a ``checkout`` in the clone root is refused ("already checked
   out at '<worktree>'") — the behind-base recovery loop + PM wedged-PR rebase
   are dead on arrival.

2. ``conventions_check_for_task`` runs the validator with ``--root <clone
   root>``; the validator reads ``(root/rel).read_bytes()`` — default-branch
   content, not the dev's worktree changes. Newly-added files are absent from
   the clone root → false pass; modified files are analyzed at stale content.

Both fix the same way: resolve the worktree via ``_worktree_for_task(clone_root,
task.id)`` + ``_ensure_worktree_for_commit`` and run the op there.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from roboco.services.git import GitService


def _service() -> GitService:
    svc = GitService.__new__(GitService)
    svc.log = MagicMock()
    svc.session = MagicMock()
    return svc


def _task(*, branch: str, task_id: UUID | None = None) -> MagicMock:
    return MagicMock(
        id=task_id or uuid4(),
        project_id=uuid4(),
        branch_name=branch,
        assigned_to=uuid4(),
    )


# --- rebase: sync_task_branch + rebase_pr_for_task route to the worktree ---


def _stub_rebase_common(svc: GitService, clone: Path) -> dict[str, list[Path]]:
    object.__setattr__(
        svc, "_project_for_task", AsyncMock(return_value=MagicMock(slug="roboco-api"))
    )
    object.__setattr__(
        svc, "_resolve_workspace_agent_id", MagicMock(return_value=uuid4())
    )
    object.__setattr__(svc, "get_workspace", AsyncMock(return_value=clone))
    object.__setattr__(
        svc, "_get_project_token_or_raise", AsyncMock(return_value="tok")
    )
    object.__setattr__(svc, "_ensure_worktree_for_commit", AsyncMock())

    cwds: list[Path] = []

    async def _run_git(workspace: Path, args: list[str], **_kw: object) -> object:
        cwds.append(Path(workspace))
        if args[:1] == ["rev-list"]:
            return MagicMock(stdout="0\n", returncode=0)
        return MagicMock(stdout="", returncode=0)

    object.__setattr__(svc, "_run_git", AsyncMock(side_effect=_run_git))
    return {"cwds": cwds}


@pytest.mark.asyncio
async def test_sync_task_branch_rebases_in_worktree_not_clone() -> None:
    svc = _service()
    task_id = uuid4()
    short = str(task_id)[:8]
    clone = Path("/tmp/ws")
    worktree = clone / ".worktrees" / short
    task = _task(branch="feature/backend/abc12345", task_id=task_id)

    state = _stub_rebase_common(svc, clone)

    await svc.sync_task_branch(task, base_branch="master")

    ensure = object.__getattribute__(svc, "_ensure_worktree_for_commit")
    ensure.assert_awaited_once()
    args = ensure.await_args.args
    assert args[0] == clone, "ensure must target the clone root"
    assert args[1] == worktree, (
        f"ensure must target the worktree {worktree}; got {args[1]}"
    )
    assert args[2] == "feature/backend/abc12345"
    assert state["cwds"], "rebase git ops must run"
    assert all(c == worktree for c in state["cwds"]), (
        f"all rebase git ops must run in the worktree {worktree}; got {state['cwds']}"
    )


@pytest.mark.asyncio
async def test_rebase_pr_for_task_rebases_in_worktree_not_clone() -> None:
    svc = _service()
    task_id = uuid4()
    short = str(task_id)[:8]
    clone = Path("/tmp/ws")
    worktree = clone / ".worktrees" / short
    task = _task(branch="feature/backend/abc12345", task_id=task_id)

    state = _stub_rebase_common(svc, clone)
    object.__setattr__(
        svc, "_parse_github_remote", MagicMock(return_value=("owner", "repo"))
    )
    object.__setattr__(
        svc,
        "_get_pr_refs",
        AsyncMock(return_value=("feature/backend/abc12345", "master")),
    )
    # rebase_pr_for_task loads the task from the DB by (pr_number, project_id).
    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = task
    session.execute = AsyncMock(return_value=result)
    svc.session = session

    await svc.rebase_pr_for_task(pr_number=42, project_id=uuid4())

    ensure = object.__getattribute__(svc, "_ensure_worktree_for_commit")
    ensure.assert_awaited_once()
    assert ensure.await_args.args[1] == worktree
    assert state["cwds"], "rebase git ops must run"
    assert all(c == worktree for c in state["cwds"]), (
        f"all rebase git ops must run in the worktree {worktree}; got {state['cwds']}"
    )


# --- conventions: validator --root points at the worktree, not the clone ---


@pytest.mark.asyncio
async def test_conventions_check_runs_validator_in_worktree_not_clone() -> None:
    svc = _service()
    task_id = uuid4()
    short = str(task_id)[:8]
    clone = Path("/tmp/ws")
    worktree = clone / ".worktrees" / short
    task = _task(branch="feature/backend/abc12345", task_id=task_id)

    object.__setattr__(svc, "_workspace_for_branch", AsyncMock(return_value=clone))
    object.__setattr__(
        svc, "list_changed_files", AsyncMock(return_value=["src/foo.py"])
    )
    object.__setattr__(svc, "_ensure_worktree_for_commit", AsyncMock())

    captured: list[Path] = []

    async def _capture_validator(
        workspace: Path, _files: list[str]
    ) -> dict[str, object]:
        captured.append(Path(workspace))
        return {"findings": [], "could_not_run": False}

    object.__setattr__(
        svc, "_run_conventions_validator", AsyncMock(side_effect=_capture_validator)
    )

    await svc.conventions_check_for_task(actor_agent_id=uuid4(), task=task)

    ensure = object.__getattribute__(svc, "_ensure_worktree_for_commit")
    ensure.assert_awaited_once()
    assert ensure.await_args.args[1] == worktree
    assert captured, "validator must run"
    assert captured[0] == worktree, (
        f"validator --root must be the worktree {worktree}, not the clone root; "
        f"got {captured[0]}"
    )
