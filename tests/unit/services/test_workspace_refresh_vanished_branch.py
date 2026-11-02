"""A clone root parked on a branch deleted upstream still refreshes its default.

Live 2026-10-28 (20 times) and 2026-11-01 (2): ``ensure_workspace: refresh
fetch returned non-zero`` with ``fatal: couldn't find remote ref
refs/heads/feature/main_pm/f32b8357`` (fe-pm, roboco-panel) and
``feature/main_pm/77e771d8`` (be-pm, roboco-api). The refresh asked for the
clone root's current branch and the default branch in one ``git fetch``; git
aborts the whole fetch on one missing ref, so the default branch was never
refreshed either, on every call. Real git end to end: a bare origin served
through a real relay, a clone under the workspaces root.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.config import settings
from roboco.services.git_relay import GitRelay
from roboco.services.workspace import WorkspaceService

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git CLI required")

_VANISHED = "feature/main_pm/f32b8357"
_GIT_FATAL = 128


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        check=check,
        capture_output=True,
        text=True,
        env=env,
    )


@dataclass(frozen=True)
class _Layout:
    origin: Path
    clone_root: Path

    def origin_main(self) -> str:
        return _git(self.origin, "rev-parse", "main").stdout.strip()

    def tracked_main(self) -> str:
        return _git(self.clone_root, "rev-parse", "origin/main").stdout.strip()


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Layout:
    """fe-pm's clone root checked out on a PM branch that origin then
    deleted (merged), while origin's main moved on."""
    monkeypatch.setattr(settings, "workspaces_root", str(tmp_path / "workspaces"))
    monkeypatch.setattr(settings, "private_data_root", str(tmp_path / "private"))
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", "-q", str(origin), str(seed))
    _git(seed, "commit", "-q", "--allow-empty", "-m", "base")
    _git(seed, "push", "-q", "origin", "main")
    _git(seed, "checkout", "-q", "-b", _VANISHED)
    _git(seed, "commit", "-q", "--allow-empty", "-m", "pm work")
    _git(seed, "push", "-q", "origin", _VANISHED)
    clone_root = tmp_path / "workspaces" / "roboco-panel" / "frontend" / "fe-pm"
    clone_root.parent.mkdir(parents=True)
    _git(tmp_path, "clone", "-q", str(origin), str(clone_root))
    _git(clone_root, "checkout", "-q", _VANISHED)
    _git(seed, "checkout", "-q", "main")
    _git(seed, "commit", "-q", "--allow-empty", "-m", "main moved on")
    _git(seed, "push", "-q", "origin", "main")
    _git(seed, "push", "-q", "origin", "--delete", _VANISHED)
    return _Layout(origin, clone_root)


def _service(layout: _Layout) -> WorkspaceService:
    svc = WorkspaceService(MagicMock())
    relay = GitRelay(
        "roboco-panel", f"file://{layout.origin}", None, default_branch="main"
    )
    object.__setattr__(svc, "_relay_for", AsyncMock(return_value=relay))
    return svc


def test_git_aborts_the_whole_fetch_when_one_ref_is_gone(layout: _Layout) -> None:
    """The premise, on real git: nothing is fetched, not even the default."""
    before = layout.tracked_main()

    fetched = _git(
        layout.clone_root,
        "fetch",
        "--no-tags",
        "--prune",
        "origin",
        _VANISHED,
        "main",
        check=False,
    )

    assert fetched.returncode == _GIT_FATAL
    assert f"couldn't find remote ref {_VANISHED}" in fetched.stderr
    assert layout.tracked_main() == before != layout.origin_main()


async def test_the_default_branch_refreshes_when_the_current_one_vanished(
    layout: _Layout,
) -> None:
    await _service(layout)._fetch_origin_best_effort(layout.clone_root, "roboco-panel")

    assert layout.tracked_main() == layout.origin_main()


async def test_the_clone_root_leaves_the_vanished_branch_for_the_default(
    layout: _Layout,
) -> None:
    """F123: the clone root parks on the default branch; the branch origin
    deleted keeps its local ref (nothing is lost), and the next refresh is a
    plain fetch of the default."""
    svc = _service(layout)

    await svc._fetch_origin_best_effort(layout.clone_root, "roboco-panel")

    current = _git(layout.clone_root, "branch", "--show-current").stdout.strip()
    assert current == "main"
    assert _git(layout.clone_root, "rev-parse", "--verify", _VANISHED).returncode == 0
