"""F019 — a git mutation op killed by ``_run_git``'s timeout orphans lock files.

``subprocess.run(..., timeout=...)`` sends SIGKILL on timeout. A git mutation
(commit / merge --ff-only / rebase / reset --hard / add) killed mid-write
orphaned ``.git/index.lock`` (+ ``HEAD.lock`` / ``refs/**.lock`` /
``packed-refs.lock``), wedging the workspace for every subsequent op —
including the next fresh-claim ``reset --hard`` — with
"Another git process seems to be running in this repository". The fix
best-effort removes stale ``.git/**/*.lock`` files in the timeout branch
before re-raising, since the git process is dead by the time the timeout
fires.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest
from roboco.exceptions import GitTimeoutError
from roboco.services.git import GitService

if TYPE_CHECKING:
    from pathlib import Path


def _svc() -> GitService:
    return GitService(MagicMock())


def _seed_locks(workspace: Path) -> dict[str, Path]:
    """Create a realistic set of orphaned git lock files under .git/."""
    git_dir = workspace / ".git"
    refs_heads = git_dir / "refs" / "heads"
    refs_heads.mkdir(parents=True, exist_ok=True)
    locks = {
        "index": git_dir / "index.lock",
        "head": git_dir / "HEAD.lock",
        "packed_refs": git_dir / "packed-refs.lock",
        "ref": refs_heads / "feature.lock",
    }
    for p in locks.values():
        p.write_text("stale")
    # A non-lock file that must be left untouched.
    keep = git_dir / "config"
    keep.write_text("[core]\n")
    return {**locks, "keep_config": keep}


@pytest.mark.asyncio
async def test_timeout_removes_stale_git_locks(tmp_path: Path, monkeypatch) -> None:
    """A timed-out git op clears orphaned .git locks before re-raising."""
    workspace = tmp_path / "ws"
    (workspace / ".git").mkdir(parents=True)
    locks = _seed_locks(workspace)

    def _boom(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["git", "commit"], timeout=5)

    monkeypatch.setattr("roboco.services.git.subprocess.run", _boom)

    with pytest.raises(GitTimeoutError):
        await _svc()._run_git(workspace, ["commit", "-m", "x"], check=True)

    # Every orphaned lock file is gone — the workspace is un-wedged.
    for name in ("index", "head", "packed_refs", "ref"):
        assert not locks[name].exists(), f"{name} lock was not cleaned up"
    # Non-lock .git files are untouched.
    assert locks["keep_config"].exists()


@pytest.mark.asyncio
async def test_timeout_lock_cleanup_is_best_effort_no_git_dir(
    tmp_path: Path, monkeypatch
) -> None:
    """A timeout with no .git/ directory must not error the cleanup path."""
    workspace = tmp_path / "ws"
    workspace.mkdir()

    def _boom(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        raise subprocess.TimeoutExpired(cmd=["git", "reset", "--hard"], timeout=5)

    monkeypatch.setattr("roboco.services.git.subprocess.run", _boom)

    with pytest.raises(GitTimeoutError):
        await _svc()._run_git(workspace, ["reset", "--hard", "origin/main"])
    # No crash — that's the assertion (best-effort cleanup on a missing .git).


@pytest.mark.asyncio
async def test_clean_exit_does_not_touch_locks(tmp_path: Path, monkeypatch) -> None:
    """A normal (non-timed-out) git op must NOT delete lock files — a concurrent
    real git process could be holding one. Cleanup is timeout-only."""
    workspace = tmp_path / "ws"
    (workspace / ".git").mkdir(parents=True)
    locks = _seed_locks(workspace)

    ok = subprocess.CompletedProcess(
        args=["git", "status"], returncode=0, stdout="", stderr=""
    )
    monkeypatch.setattr("roboco.services.git.subprocess.run", lambda *_a, **_k: ok)
    # _ensure_agent_owned runs after a clean exit — no-op it.
    monkeypatch.setattr(
        "roboco.services.workspace._ensure_agent_owned", lambda _ws: None
    )

    await _svc()._run_git(workspace, ["status"], check=True)

    for name in ("index", "head", "packed_refs", "ref"):
        assert locks[name].exists(), f"{name} lock was wrongly removed on clean exit"
