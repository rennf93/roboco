"""``resolve_git_dir`` — the worktree ``.git``-is-a-file helper (F123, Phase A).

A linked worktree's ``.git`` is a *file* (a ``gitdir: <path>`` pointer into the
clone root's ``.git/worktrees/<id>/``), not a directory. Every site that today
does ``workspace / ".git"`` and assumes a directory breaks under worktrees. This
helper is the single chokepoint that follows the pointer.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from roboco.services.git import resolve_git_dir


def _git(cwd: Path, *args: str) -> str:
    env = {
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
        env={**__import__("os").environ, **env},
    ).stdout


def _init_clone(clone: Path) -> None:
    clone.mkdir(parents=True)
    _git(clone, "init", "-b", "main")
    (clone / "README.md").write_text("hi\n")
    _git(clone, "add", "README.md")
    _git(clone, "commit", "-m", "init")


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    c = tmp_path / "clone"
    _init_clone(c)
    return c


pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git CLI required for worktree tests"
)


def test_resolve_git_dir_clone_root_returns_dot_git_dir(clone: Path) -> None:
    # The clone root's .git is a real directory.
    resolved = resolve_git_dir(clone)
    assert resolved == clone / ".git"
    assert resolved.is_dir()


def test_resolve_git_dir_worktree_follows_gitdir_pointer(clone: Path) -> None:
    # A linked worktree's .git is a FILE (gitdir pointer). The helper must
    # follow it into clone/.git/worktrees/<id>/.
    wt = clone / ".worktrees" / "t1"
    _git(clone, "worktree", "add", str(wt), "-b", "feature/t1")

    assert (wt / ".git").is_file(), "linked worktree .git must be a file"

    resolved = resolve_git_dir(wt)
    assert resolved is not None
    # Points into the clone's worktree admin area, not the worktree's own .git file.
    assert resolved.is_dir()
    assert resolved.parent.parent == clone / ".git"
    assert resolved.parent.name == "worktrees"
    # Sanity: the gitdir file points here.
    pointer = (wt / ".git").read_text().strip()
    assert pointer.startswith("gitdir: ")
    assert Path(pointer[len("gitdir: ") :].strip()) == resolved


def test_resolve_git_dir_no_git_returns_none(tmp_path: Path) -> None:
    # A bare dir with no .git: callers (e.g. _remove_stale_git_locks) must get
    # None and bail cleanly, not crash on a missing path.
    bare = tmp_path / "no-repo"
    bare.mkdir()
    assert resolve_git_dir(bare) is None


def test_resolve_git_dir_worktree_locks_are_reachable(clone: Path) -> None:
    # The motivating caller: _remove_stale_git_locks must be able to rglob
    # *.lock inside a WORKTREE's git dir. Proves the pointer-follow resolves to
    # a rglob-able directory.
    wt = clone / ".worktrees" / "t1"
    _git(clone, "worktree", "add", str(wt), "-b", "feature/t1")
    resolved = resolve_git_dir(wt)
    assert resolved is not None
    (resolved / "index.lock").write_text("fake")
    # rglob reaches it (this is what _remove_stale_git_locks will do).
    locks = list(resolved.rglob("*.lock"))
    assert any(p.name == "index.lock" for p in locks)
