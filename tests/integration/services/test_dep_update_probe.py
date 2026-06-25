"""dry_upgrade_changes_lockfile — the read-only lockfile-diff probe.

Runs the project's dep_update_command in an isolated clone of the read clone and
reports whether a lockfile path got dirty — without ever mutating the read clone
or committing/pushing. Fail-safe: a null/failing command returns False.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.services.workspace import WorkspaceService

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


def _svc(read_clone: Path) -> WorkspaceService:
    svc = WorkspaceService.__new__(WorkspaceService)
    svc.ensure_read_clone = AsyncMock(return_value=read_clone)  # type: ignore[method-assign]
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
