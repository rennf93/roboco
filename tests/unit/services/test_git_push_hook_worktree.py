"""A task push runs the project's pre-push hook on the task's code.

The task branch is pushed by name, from the developer's clone root (F123:
parked on another branch). git runs ``pre-push`` in the directory the push
starts from, so the hook found and run there was the clone root's: its
version of ``.githooks/pre-push``, checking the clone root's tree, not the
commits being pushed. The push now starts in the task's worktree, as the
commit does. Real git end to end (a bare origin through the relay, a clone
under the workspaces root); only the throwaway container is a local runner.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.services import project_hooks
from roboco.services.git import GitService
from roboco.services.workspace import WorkspaceService
from roboco.services.workspace_container import (
    WorkspaceCommand,
    WorkspaceContainerResult,
)
from tests.unit.local_origin_relay import serve_origin_through_relay

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git CLI required")

_SUPPORT = Path(__file__).parents[3] / "docker" / "git-support"
_BRANCH = "feature/backend/abcd1234"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


async def _run_hook_here(
    cwd: Path,
    command: WorkspaceCommand,
    *,
    timeout: float,
    env: dict[str, str] | None = None,
    merge_stderr: bool = False,
) -> WorkspaceContainerResult:
    done = subprocess.run(
        list(command.argv),
        cwd=cwd,
        env={**os.environ, **(env or {})},
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    out = done.stdout + (done.stderr if merge_stderr else "")
    return WorkspaceContainerResult(done.returncode, out, "")


def _hook(tree: Path, body: str) -> None:
    hook = tree / ".githooks" / "pre-push"
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text(f'#!/bin/sh\npwd > "{tree}/.pre-push.ran-in"\n{body}\n')
    hook.chmod(0o755)
    _git(tree, "add", ".githooks/pre-push")


@pytest.fixture
def clone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """be-dev-1's clone root on main, whose pre-push refuses everything; the
    task branch in its worktree carries a pre-push that passes."""
    monkeypatch.setattr(settings, "workspaces_root", str(tmp_path / "workspaces"))
    support = tmp_path / "support"
    shutil.copytree(_SUPPORT, support)
    monkeypatch.setattr(settings, "git_support_dir", str(support))
    monkeypatch.setattr(project_hooks, "run_in_workspace_container", _run_hook_here)
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True
    )
    serve_origin_through_relay(monkeypatch, tmp_path, origin)
    clone = tmp_path / "workspaces" / "proj" / "backend" / "be-dev-1"
    clone.parent.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(clone)], check=True)
    _git(clone, "config", "user.name", "be-dev-1")
    _git(clone, "config", "user.email", "be-dev-1@roboco.tech")
    _hook(clone, 'echo "the clone root is not what is pushed"; exit 1')
    _git(clone, "commit", "-qm", "base")
    _git(clone, "push", "-q", str(origin), "main")
    _git(clone, "config", "core.hooksPath", ".githooks")
    return clone


async def test_the_task_push_runs_pre_push_in_the_task_worktree(
    clone: Path, tmp_path: Path
) -> None:
    task = SimpleNamespace(id=uuid4(), branch_name=_BRANCH, project_id=uuid4())
    worktree = clone / ".worktrees" / str(task.id)[:8]
    _git(clone, "worktree", "add", "-q", "-b", _BRANCH, str(worktree), "main")
    _hook(worktree, "exit 0")
    _git(worktree, "commit", "-qm", f"[{str(task.id)[:8]}] the task's work")
    svc = GitService(MagicMock())
    object.__setattr__(
        svc, "_assert_task_owned_with_branch", AsyncMock(return_value=task)
    )
    object.__setattr__(
        svc, "_project_for_task", AsyncMock(return_value=SimpleNamespace(slug="proj"))
    )
    object.__setattr__(svc, "get_workspace", AsyncMock(return_value=clone))
    object.__setattr__(svc, "_run_codegen_and_commit", AsyncMock())

    with patch("roboco.services.git.get_workspace_service", WorkspaceService):
        await svc.push_task_branch(uuid4(), task.id)

    assert (worktree / ".pre-push.ran-in").read_text().strip() == str(worktree)
    assert _git(tmp_path / "origin.git", "rev-parse", _BRANCH) == _git(
        worktree, "rev-parse", "HEAD"
    )


async def test_without_an_attached_worktree_the_push_stays_in_the_clone_root(
    clone: Path,
) -> None:
    """A pruned worktree is never re-added for a push: the clone root and
    its push-by-name recovery stay in charge."""
    task = SimpleNamespace(id=uuid4(), branch_name=_BRANCH, project_id=uuid4())
    _git(clone, "branch", _BRANCH, "main")

    tree = await GitService(MagicMock())._attached_task_tree(clone, task)

    assert tree == clone
    assert not (clone / ".worktrees").exists()
