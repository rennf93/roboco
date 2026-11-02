"""What ``WorkspaceService.remove_worktree`` keeps and what it destroys.

The dispatcher's ``_release_claim_to_pending`` released a claim through
``TaskService.unclaim_for_reaper``, which also removes the task's worktree. This
pins, against a real git clone, what that removal does to an author's work:
commits on the task branch survive (they live in the clone's shared repository,
under a branch ref the removal never touches), while uncommitted edits and
untracked files in the worktree are deleted (``git worktree remove --force``).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from roboco.services.workspace import WorkspaceService

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git CLI required for worktree tests"
)

_BRANCH = "feature/backend/106f46e3"


def _git(cwd: Path, *args: str) -> str:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout


@pytest.fixture
def worktree_with_work(tmp_path: Path) -> tuple[Path, Path, str]:
    """A clone whose task worktree holds one commit that was never pushed, an
    uncommitted edit to a tracked file and an untracked file."""
    clone = tmp_path / "clone"
    clone.mkdir()
    _git(clone, "init", "-b", "main")
    (clone / "app.py").write_text("x = 1\n")
    _git(clone, "add", "app.py")
    _git(clone, "commit", "-m", "init")
    worktree = clone / ".worktrees" / "106f46e3"
    _git(clone, "worktree", "add", "-b", _BRANCH, str(worktree), "main")
    (worktree / "app.py").write_text("x = 2\n")
    _git(worktree, "commit", "-am", "[106f46e3] committed, never pushed")
    commit = _git(worktree, "rev-parse", "HEAD").strip()
    (worktree / "app.py").write_text("x = 3\n")
    (worktree / "new_module.py").write_text("y = 1\n")
    return clone, worktree, commit


@pytest.mark.asyncio
async def test_removal_keeps_commits_and_destroys_uncommitted_work(
    worktree_with_work: tuple[Path, Path, str],
) -> None:
    clone, worktree, commit = worktree_with_work

    await WorkspaceService(MagicMock()).remove_worktree(clone, worktree)

    assert _git(clone, "rev-parse", _BRANCH).strip() == commit
    assert not worktree.exists()
    assert not (worktree / "new_module.py").exists()
    assert "x = 3" not in _git(clone, "show", f"{_BRANCH}:app.py")
