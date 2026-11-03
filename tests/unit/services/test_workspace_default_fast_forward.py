"""The clone root's default-branch checkout follows origin after a refresh.

The refresh fetch moved ``origin/<default>`` but never the clone root's own
checkout of the default branch, so the clone root stayed on whatever commit
it was cloned at. The shared ``.venv`` is installed from that checkout's
lockfile (``install_dev_deps`` runs in the clone root), and with the worktree
import hook a worktree imports its own source on those stale dependencies.
After a refresh the clone root is fast-forwarded when, and only when, it sits
on the default branch with no tracked change: ``--ff-only``, never another
branch, never a dirty tree. Real git end to end (a bare origin through a
real relay, a clone under the workspaces root).
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
    ).stdout.strip()


@dataclass(frozen=True)
class _Layout:
    origin: Path
    seed: Path
    clone_root: Path

    def advance_origin_lockfile(self) -> str:
        (self.seed / "uv.lock").write_text("version = 2\n")
        _git(self.seed, "commit", "-qam", "bump deps")
        _git(self.seed, "push", "-q", "origin", "main")
        return _git(self.seed, "rev-parse", "HEAD")


@pytest.fixture
def layout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Layout:
    monkeypatch.setattr(settings, "workspaces_root", str(tmp_path / "workspaces"))
    monkeypatch.setattr(settings, "private_data_root", str(tmp_path / "private"))
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", "-q", str(origin), str(seed))
    (seed / "uv.lock").write_text("version = 1\n")
    _git(seed, "add", "uv.lock")
    _git(seed, "commit", "-qm", "base")
    _git(seed, "push", "-q", "origin", "main")
    clone_root = tmp_path / "workspaces" / "roboco-api" / "backend" / "be-dev-1"
    clone_root.parent.mkdir(parents=True)
    _git(tmp_path, "clone", "-q", str(origin), str(clone_root))
    return _Layout(origin, seed, clone_root)


async def _refresh(layout: _Layout) -> None:
    svc = WorkspaceService(MagicMock())
    relay = GitRelay(
        "roboco-api", f"file://{layout.origin}", None, default_branch="main"
    )
    object.__setattr__(svc, "_relay_for", AsyncMock(return_value=relay))
    object.__setattr__(svc, "_project_default_branch", AsyncMock(return_value="main"))
    await svc._fetch_origin_best_effort(layout.clone_root, "roboco-api")


async def test_a_clean_clone_root_on_the_default_branch_follows_origin(
    layout: _Layout,
) -> None:
    tip = layout.advance_origin_lockfile()

    await _refresh(layout)

    assert _git(layout.clone_root, "rev-parse", "main") == tip
    assert (layout.clone_root / "uv.lock").read_text() == "version = 2\n"


async def test_a_dirty_clone_root_is_never_touched(layout: _Layout) -> None:
    before = _git(layout.clone_root, "rev-parse", "main")
    (layout.clone_root / "uv.lock").write_text("local edit\n")
    layout.advance_origin_lockfile()

    await _refresh(layout)

    assert _git(layout.clone_root, "rev-parse", "main") == before
    assert (layout.clone_root / "uv.lock").read_text() == "local edit\n"


async def test_a_clone_root_on_another_branch_is_never_touched(
    layout: _Layout,
) -> None:
    _git(layout.clone_root, "checkout", "-q", "-b", "feature/backend/abc12345")
    _git(layout.clone_root, "push", "-q", "origin", "feature/backend/abc12345")
    before_main = _git(layout.clone_root, "rev-parse", "main")
    layout.advance_origin_lockfile()

    await _refresh(layout)

    current = _git(layout.clone_root, "branch", "--show-current")
    assert current == "feature/backend/abc12345"
    assert _git(layout.clone_root, "rev-parse", "main") == before_main


async def test_a_default_branch_with_local_commits_is_left_alone(
    layout: _Layout,
) -> None:
    """--ff-only: a diverged local default is never merged or reset."""
    _git(layout.clone_root, "commit", "-q", "--allow-empty", "-m", "local only")
    local = _git(layout.clone_root, "rev-parse", "main")
    layout.advance_origin_lockfile()

    await _refresh(layout)

    assert _git(layout.clone_root, "rev-parse", "main") == local


async def test_a_clone_root_follows_the_project_default_not_a_stale_origin_head(
    layout: _Layout,
) -> None:
    """origin/HEAD is frozen at clone time; the project's default moved."""
    _git(layout.seed, "checkout", "-q", "-b", "slave")
    _git(layout.seed, "push", "-q", "origin", "slave")
    _git(layout.clone_root, "fetch", "-q", "origin", "slave")
    _git(layout.clone_root, "checkout", "-q", "-b", "slave", "origin/slave")
    (layout.seed / "uv.lock").write_text("version = 3\n")
    _git(layout.seed, "commit", "-qam", "advance slave")
    _git(layout.seed, "push", "-q", "origin", "slave")
    tip = _git(layout.seed, "rev-parse", "HEAD")
    assert _git(layout.clone_root, "symbolic-ref", "refs/remotes/origin/HEAD") == (
        "refs/remotes/origin/main"
    )
    svc = WorkspaceService(MagicMock())
    relay = GitRelay("roboco-api", f"file://{layout.origin}", None)
    object.__setattr__(svc, "_relay_for", AsyncMock(return_value=relay))
    object.__setattr__(svc, "_project_default_branch", AsyncMock(return_value="slave"))

    await svc._fetch_origin_best_effort(layout.clone_root, "roboco-api")

    assert _git(layout.clone_root, "rev-parse", "slave") == tip


async def test_a_failed_project_lookup_falls_back_to_origin_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refresh never raises: a failed project read keeps origin/HEAD."""
    session = MagicMock()
    session.begin_nested.return_value.__aexit__.return_value = False
    projects = MagicMock(get_by_slug=AsyncMock(side_effect=RuntimeError("db down")))
    monkeypatch.setattr(
        "roboco.services.project.get_project_service", lambda _session: projects
    )

    assert await WorkspaceService(session)._project_default_branch("roboco-api") is None
    session.begin_nested.assert_called_once_with()
