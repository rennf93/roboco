"""Spawn-time refresh of the base clone's own checkout (#692 family, item 3).

The per-task worktree heal (``_refresh_present_worktree``) leaves the clone
root's own checkout untouched — and that checkout can sit arbitrarily far
behind origin (a live reader clone was ~19 wall hours behind), so raw file
reads disagreed with the origin-preferring git-log reader (#690).
``refresh_base_clone_checkout`` closes that gap for non-author roles. Tested
against a real tmp git clone with a real bare origin — no DB, no mocks of git.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.services.workspace import WorkspaceService

if TYPE_CHECKING:
    from pathlib import Path


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


def _service() -> WorkspaceService:
    return WorkspaceService(MagicMock())


@pytest.fixture
def repo(tmp_path: Path) -> tuple[Path, Path]:
    """A work clone of a bare origin, with a seed repo to push new commits from."""
    seed = tmp_path / "seed"
    seed.mkdir()
    _git(seed, "init", "-b", "main")
    (seed / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    _git(seed, "add", "pyproject.toml")
    _git(seed, "commit", "-m", "init")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "--bare", str(seed), str(origin)], check=True)
    _git(seed, "remote", "add", "origin", str(origin))
    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(origin), str(work)], check=True)
    return work, seed


def _origin_tip(seed: Path) -> str:
    return _git(seed, "rev-parse", "HEAD").strip()


def _push_new_commit(seed: Path, filename: str, content: str) -> str:
    (seed / filename).write_text(content)
    _git(seed, "add", filename)
    _git(seed, "commit", "-m", f"add {filename}")
    _git(seed, "push", "origin", "main")
    return _origin_tip(seed)


async def _refresh(work: Path) -> None:
    # Fetch for real via plain git (the service's _fetch_branch_ref only wraps
    # it with the project's decrypted token, which needs a DB we don't have
    # here) — then run the refresh with that wrapper mocked out.
    _git(work, "fetch", "origin")
    svc = _service()
    with patch.object(WorkspaceService, "_fetch_branch_ref", new=AsyncMock()):
        await svc.refresh_base_clone_checkout(work, "proj")


pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git CLI required for worktree tests"
)


async def test_refresh_fast_forwards_strictly_behind_clone(
    repo: tuple[Path, Path],
) -> None:
    # The acceptance pin: a reader clone parked on an old tip sees origin
    # content on its first file read after the spawn-time refresh.
    work, seed = repo
    tip = _push_new_commit(seed, "new.txt", "from origin\n")
    assert _git(work, "rev-parse", "HEAD").strip() != tip

    await _refresh(work)

    assert _git(work, "rev-parse", "HEAD").strip() == tip
    assert (work / "new.txt").read_text() == "from origin\n"


async def test_refresh_hard_resets_diverged_clone(repo: tuple[Path, Path]) -> None:
    # Readers never author — a diverged checkout can only be a stale prior
    # state, so it resets to origin even though local history exists.
    work, seed = repo
    tip = _push_new_commit(seed, "origin.txt", "origin\n")
    (work / "local.txt").write_text("local\n")
    _git(work, "add", "local.txt")
    _git(work, "commit", "-m", "local work")
    local_tip = _git(work, "rev-parse", "HEAD").strip()

    await _refresh(work)

    assert _git(work, "rev-parse", "HEAD").strip() == tip
    assert _git(work, "rev-parse", "HEAD").strip() != local_tip
    assert (work / "origin.txt").read_text() == "origin\n"


async def test_refresh_leaves_in_sync_clone_untouched(repo: tuple[Path, Path]) -> None:
    work, _seed = repo
    before = _git(work, "rev-parse", "HEAD").strip()

    await _refresh(work)

    assert _git(work, "rev-parse", "HEAD").strip() == before


async def test_refresh_leaves_strictly_ahead_clone_untouched(
    repo: tuple[Path, Path],
) -> None:
    # Conservative: never rewrite a checkout that has commits origin lacks.
    work, _seed = repo
    (work / "local.txt").write_text("local\n")
    _git(work, "add", "local.txt")
    _git(work, "commit", "-m", "local work")
    before = _git(work, "rev-parse", "HEAD").strip()

    await _refresh(work)

    assert _git(work, "rev-parse", "HEAD").strip() == before


async def test_refresh_skips_detached_head(repo: tuple[Path, Path]) -> None:
    work, seed = repo
    tip = _push_new_commit(seed, "new.txt", "from origin\n")
    _git(work, "checkout", "--detach", "HEAD")

    await _refresh(work)

    assert _git(work, "rev-parse", "HEAD").strip() != tip
