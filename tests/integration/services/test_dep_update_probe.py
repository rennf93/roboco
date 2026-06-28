"""dry_upgrade_changes_lockfile — the read-only lockfile-diff probe.

Runs the project's dep_update_command in an isolated clone of the read clone and
reports whether a lockfile path got dirty — without ever mutating the read clone
or committing/pushing. Fail-safe: a null/failing command returns False.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.services.workspace import WorkspaceService, _ensure_lock_for

if TYPE_CHECKING:
    from pathlib import Path


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )


def _make_read_clone(tmp_path: Path) -> Path:
    repo = tmp_path / "readclone"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "uv.lock").write_text("version = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    return repo


def _svc(read_clone: Path) -> Any:
    svc: Any = WorkspaceService.__new__(WorkspaceService)
    svc.ensure_read_clone = AsyncMock(return_value=read_clone)
    return svc


def _project(command: str | None, paths: list[str] | None = None) -> MagicMock:
    return MagicMock(slug="p", dep_update_command=command, dep_update_paths=paths)


@pytest.mark.asyncio
async def test_dirtying_a_lockfile_returns_true(tmp_path: Path) -> None:
    read_clone = _make_read_clone(tmp_path)
    svc = _svc(read_clone)
    cmd = "python3 -c \"open('uv.lock','a').write('x')\""

    assert await svc.dry_upgrade_changes_lockfile(_project(cmd)) is True

    # The read clone itself is never mutated by the probe.
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=str(read_clone),
        capture_output=True,
        text=True,
        check=True,
    )
    assert status.stdout.strip() == ""


@pytest.mark.asyncio
async def test_noop_command_returns_false(tmp_path: Path) -> None:
    svc = _svc(_make_read_clone(tmp_path))
    assert (
        await svc.dry_upgrade_changes_lockfile(_project('python3 -c "pass"')) is False
    )


@pytest.mark.asyncio
async def test_null_command_returns_false(tmp_path: Path) -> None:
    svc = _svc(_make_read_clone(tmp_path))
    assert await svc.dry_upgrade_changes_lockfile(_project(None)) is False


@pytest.mark.asyncio
async def test_failing_command_returns_false(tmp_path: Path) -> None:
    svc = _svc(_make_read_clone(tmp_path))
    cmd = 'python3 -c "import sys; sys.exit(1)"'
    assert await svc.dry_upgrade_changes_lockfile(_project(cmd)) is False


@pytest.mark.asyncio
async def test_explicit_dep_update_paths_scope(tmp_path: Path) -> None:
    read_clone = _make_read_clone(tmp_path)
    svc = _svc(read_clone)
    # Command dirties uv.lock, but we only watch a different lockfile → False.
    cmd = "python3 -c \"open('uv.lock','a').write('x')\""
    project = _project(cmd, paths=["pnpm-lock.yaml"])
    assert await svc.dry_upgrade_changes_lockfile(project) is False


@pytest.mark.asyncio
async def test_probe_holds_read_clone_lock_across_local_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F116: the dep-update probe must hold the read-clone lock for the
    duration of the local ``git clone --local`` from the read clone, so a
    concurrent ``ensure_read_clone`` → ``_sync_read_clone`` (fetch + hard-reset
    to origin's default branch) cannot mutate the read clone mid-clone. The
    lock is released before the upgrade runs on the independent copy (the
    upgrade never touches the read clone, so holding the lock past the clone
    would needlessly block conventions reads for the upgrade duration)."""
    read_clone = _make_read_clone(tmp_path)
    svc = _svc(read_clone)
    # Unique slug → a fresh lock not shared with any other test.
    project = MagicMock(
        slug="f116-probe", dep_update_command="python3 -c pass", dep_update_paths=None
    )
    lock = _ensure_lock_for("f116-probe", "_meta-conventions")
    assert not lock.locked()

    def slow_clone(read_clone: Path, clone_dir: Path, timeout: float) -> None:
        # Real local clone so the dir is valid, then hold the lock a while so
        # the test coroutine can observe the lock is held mid-clone.
        subprocess.run(
            [
                "git",
                "clone",
                "--local",
                "--no-hardlinks",
                str(read_clone),
                str(clone_dir),
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=True,
        )
        time.sleep(0.4)

    def noop_probe(_clone_dir: Path, _command: str, _lock_paths: list[str]) -> bool:
        return False

    monkeypatch.setattr(WorkspaceService, "_clone_local_into", staticmethod(slow_clone))
    monkeypatch.setattr(
        WorkspaceService, "_probe_lockfile_on_clone", staticmethod(noop_probe)
    )

    probe_task = asyncio.create_task(svc.dry_upgrade_changes_lockfile(project))
    # ensure_read_clone is mocked (instant) → the probe immediately enters the
    # lock + clone step. Give it a beat, then assert the lock is held.
    await asyncio.sleep(0.1)
    assert lock.locked(), "read-clone lock must be held during the local-clone step"
    await asyncio.wait_for(probe_task, timeout=3)
    assert not lock.locked(), "lock released after the clone step (upgrade needs none)"
