"""uv resolves the clone-root ``.venv`` from a per-task worktree (F123, risk #1).

The highest unknown in the worktree design: uv discovers ``.venv`` next to the
worktree's ``pyproject.toml``. A worktree has no ``.venv`` of its own, so
without the ``worktree/.venv -> ../../.venv`` symlink uv would re-sync a fresh
venv per task (slow + divergent toolchains). This proves the symlink makes uv
resolve the shared clone-root venv when invoked from the worktree cwd — the
exact resolution path an agent's ``make quality`` hits.

Real subprocesses (git + uv); skipped when ``uv`` is absent.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from roboco.services.workspace import WorkspaceService

pytestmark = pytest.mark.skipif(
    shutil.which("uv") is None, reason="uv CLI not installed"
)


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


def _run(cwd: Path, *cmd: str) -> str:
    # Scrub the parent uv environment so uv discovers fresh from the worktree
    # cwd instead of inheriting the test process's VIRTUAL_ENV (which would
    # mask the worktree .venv symlink and false-pass/fail the resolution).
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "UV_PYTHON_INSTALL_DIR"}
    }
    return subprocess.run(
        list(cmd),
        check=True,
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env=env,
    ).stdout


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    c = tmp_path / "clone"
    c.mkdir(parents=True)
    _git(c, "init", "-b", "main")
    (c / "pyproject.toml").write_text("[project]\nname = 'x'\nversion = '0'\n")
    _git(c, "add", "pyproject.toml")
    _git(c, "commit", "-m", "init")
    # Real clone-root venv — the symlink target uv must resolve to.
    _run(c, "uv", "venv", ".venv")
    return c


def _service() -> WorkspaceService:
    return WorkspaceService(MagicMock())


def test_worktree_venv_symlink_points_at_clone_root(clone: Path) -> None:
    svc = _service()
    worktree = clone / ".worktrees" / "abc12345"
    svc._link_shared_venv(worktree, clone)

    link = worktree / ".venv"
    assert link.is_symlink(), "worktree/.venv must be a symlink"
    assert link.readlink() == Path("../../.venv")
    # Resolves to the clone-root venv, not a per-worktree one.
    assert link.resolve() == (clone / ".venv").resolve()


async def test_uv_resolves_clone_root_venv_from_worktree(clone: Path) -> None:
    svc = _service()
    worktree = clone / ".worktrees" / "abc12345"
    with patch("roboco.services.workspace._ensure_agent_owned"):
        await svc.ensure_worktree(clone, worktree, "feature/x", "main")

    # uv invoked from the worktree must use the clone-root venv's python,
    # not create/sync a worktree-local one. Loads the worktree pyproject
    # (which has no deps) and skips sync.
    out = _run(
        worktree,
        "uv",
        "run",
        "--no-sync",
        "python",
        "-c",
        "import sys; print(sys.executable)",
    ).strip()
    clone_venv_python = (clone / ".venv" / "bin" / "python").resolve()
    assert Path(out).resolve() == clone_venv_python, (
        f"uv must resolve the clone-root venv from the worktree; "
        f"got {out}, expected {clone_venv_python}"
    )


async def test_clone_root_stays_on_default_after_worktree_add(clone: Path) -> None:
    # THE F123 assertion: cutting a task worktree does NOT move the clone root
    # off the default branch. A second task's worktree is independent.
    svc = _service()
    wt1 = clone / ".worktrees" / "task1"
    wt2 = clone / ".worktrees" / "task2"
    with patch("roboco.services.workspace._ensure_agent_owned"):
        await svc.ensure_worktree(clone, wt1, "feature/a", "main")
        await svc.ensure_worktree(clone, wt2, "feature/b", "main")

    clone_head = _git(clone, "branch", "--show-current").strip()
    assert clone_head == "main", (
        f"clone root must stay on default after worktree add; got {clone_head}"
    )
    assert _git(wt1, "branch", "--show-current").strip() == "feature/a"
    assert _git(wt2, "branch", "--show-current").strip() == "feature/b"
