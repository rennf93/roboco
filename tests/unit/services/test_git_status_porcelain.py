"""get_status must not misreport an unstaged deletion as staged.

Porcelain encodes the index (staged) state in column 0 and the worktree state
in column 1. A worktree-only change has a SPACE in column 0 (e.g. " D file" =
unstaged deletion). The old code ran stdout.strip() before splitting, which ate
the leading space on the first line, turning " D file" into "D file" — parsed as
a STAGED deletion. That false "staged" caused 6 wasted QA cycles when a dev
deleted a file but had not staged it. Regression test: the deletion must land in
unstaged, never staged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from roboco.services.git import GitService


def _git_service() -> GitService:
    return GitService.__new__(GitService)


def _result(returncode: int = 0, stdout: str = "") -> Any:
    return type("R", (), {"returncode": returncode, "stdout": stdout})()


def _fake_run_with_status(porcelain: str) -> Any:
    async def fake_run(_ws: Any, args: list[str], **_kw: Any) -> Any:
        if args[:2] == ["branch", "--show-current"]:
            return _result(stdout="feature/x\n")
        if args[:2] == ["status", "--porcelain"]:
            return _result(stdout=porcelain)
        return _result(returncode=1)  # ahead/behind rev-list -> treated as 0,0

    return fake_run


@pytest.mark.asyncio
async def test_unstaged_deletion_first_line_not_reported_as_staged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = _git_service()
    monkeypatch.setattr(
        svc, "_run_git", _fake_run_with_status(" D piragi_patches.py\n")
    )
    monkeypatch.setattr(svc, "_ahead_behind", AsyncMock(return_value=(0, 0)))

    _branch, has_changes, staged, unstaged, _untracked, _a, _b = await svc.get_status(
        Path("/tmp/ws")
    )

    assert "piragi_patches.py" in unstaged
    assert "piragi_patches.py" not in staged
    assert has_changes is True


@pytest.mark.asyncio
async def test_staged_deletion_still_reported_as_staged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A genuine staged deletion ('D  file') must still read as staged."""
    svc = _git_service()
    monkeypatch.setattr(svc, "_run_git", _fake_run_with_status("D  gone.py\n"))
    monkeypatch.setattr(svc, "_ahead_behind", AsyncMock(return_value=(0, 0)))

    _branch, _has, staged, unstaged, _untracked, _a, _b = await svc.get_status(
        Path("/tmp/ws")
    )

    assert "gone.py" in staged
    assert "gone.py" not in unstaged


@pytest.mark.asyncio
async def test_first_line_unstaged_modify_not_misread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The strip() bug hit any worktree-only first line, not just deletions."""
    svc = _git_service()
    monkeypatch.setattr(svc, "_run_git", _fake_run_with_status(" M app.py\n"))
    monkeypatch.setattr(svc, "_ahead_behind", AsyncMock(return_value=(0, 0)))

    _branch, _has, staged, unstaged, _untracked, _a, _b = await svc.get_status(
        Path("/tmp/ws")
    )

    assert "app.py" in unstaged
    assert "app.py" not in staged
