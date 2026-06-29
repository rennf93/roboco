"""Per-task worktree lifecycle primitives (F123, Phase A — additive, not yet wired).

``ensure_worktree`` / ``ensure_worktree_for_resume`` / ``remove_worktree`` on
WorkspaceService. These are the pure primitives Phase B's claim/resume flow will
call. Tested against a real tmp git clone — no DB, no Docker, no mocks of git.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import TYPE_CHECKING
from unittest.mock import patch

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


def _init_clone(clone: Path) -> None:
    clone.mkdir(parents=True)
    _git(clone, "init", "-b", "main")
    (clone / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    _git(clone, "add", "pyproject.toml")
    _git(clone, "commit", "-m", "init")


def _service() -> WorkspaceService:
    return WorkspaceService(
        __import__("unittest.mock", fromlist=["MagicMock"]).MagicMock()
    )


@pytest.fixture
def clone(tmp_path: Path) -> Path:
    c = tmp_path / "clone"
    _init_clone(c)
    return c


pytestmark = pytest.mark.skipif(
    shutil.which("git") is None, reason="git CLI required for worktree tests"
)


async def test_ensure_worktree_creates_linked_worktree_on_new_branch(
    clone: Path,
) -> None:
    svc = _service()
    wt = clone / ".worktrees" / "a3c40fe7"

    with patch("roboco.services.workspace._ensure_agent_owned"):
        await svc.ensure_worktree(clone, wt, "feature/a3c40fe7", "main")

    assert (wt / ".git").is_file(), "linked worktree .git must be a gitdir file"
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == "feature/a3c40fe7"


async def test_ensure_worktree_symlinks_venv_to_clone_root(clone: Path) -> None:
    # uv discovers .venv next to pyproject.toml IN the worktree. Without a
    # symlink to the clone-root .venv, uv re-syncs per worktree (bad). The
    # symlink lets uv resolve the shared clone-root venv.
    svc = _service()
    (clone / ".venv").mkdir()  # clone-root venv exists from install_dev_deps
    wt = clone / ".worktrees" / "a3c40fe7"

    with patch("roboco.services.workspace._ensure_agent_owned"):
        await svc.ensure_worktree(clone, wt, "feature/a3c40fe7", "main")

    venv_link = wt / ".venv"
    assert venv_link.is_symlink(), (
        "worktree .venv must be a symlink to clone-root .venv"
    )
    assert venv_link.resolve() == (clone / ".venv").resolve()


async def test_ensure_worktree_no_dangling_venv_symlink_when_clone_root_venv_missing(
    clone: Path,
) -> None:
    # If the clone-root venv is not yet provisioned, the worktree .venv symlink
    # must NOT be created — a dangling ../../.venv symlink makes uv error or
    # re-sync a worktree-local venv that the lexists guard then can't replace.
    # install_dev_deps provisions clone_root/.venv before the first worktree
    # add on the fresh-claim path, so this only fires in the near-zero gap.
    svc = _service()
    assert not (clone / ".venv").exists()
    wt = clone / ".worktrees" / "a3c40fe7"

    with patch("roboco.services.workspace._ensure_agent_owned"):
        await svc.ensure_worktree(clone, wt, "feature/a3c40fe7", "main")

    link = wt / ".venv"
    assert not link.is_symlink(), (
        "no symlink when clone-root venv is absent (would dangle)"
    )
    assert not link.exists()


async def test_ensure_worktree_links_venv_once_clone_root_venv_provisioned(
    clone: Path,
) -> None:
    # Self-heal: a worktree claimed before the clone-root venv existed gets no
    # symlink; once install_dev_deps provisions clone_root/.venv, the next
    # ensure (resume path) links it.
    svc = _service()
    wt = clone / ".worktrees" / "a3c40fe7"

    with patch("roboco.services.workspace._ensure_agent_owned"):
        await svc.ensure_worktree(clone, wt, "feature/a3c40fe7", "main")
    assert not (wt / ".venv").is_symlink()

    (clone / ".venv").mkdir()  # install_dev_deps completes
    with patch("roboco.services.workspace._ensure_agent_owned"):
        await svc.ensure_worktree_for_resume(clone, wt, "feature/a3c40fe7")

    link = wt / ".venv"
    assert link.is_symlink()
    assert link.resolve() == (clone / ".venv").resolve()


async def test_ensure_worktree_idempotent_on_existing_worktree(clone: Path) -> None:
    svc = _service()
    wt = clone / ".worktrees" / "a3c40fe7"

    with patch("roboco.services.workspace._ensure_agent_owned"):
        await svc.ensure_worktree(clone, wt, "feature/a3c40fe7", "main")
        # Second call must be a no-op, not an error ("already exists").
        await svc.ensure_worktree(clone, wt, "feature/a3c40fe7", "main")

    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == "feature/a3c40fe7"


async def test_ensure_worktree_chowns_both_worktree_and_clone_root(clone: Path) -> None:
    # The two-target ownership invariant: the worktree working tree AND the
    # clone root (shared .git/worktrees/<id>/, .venv, .uv-python) must be
    # agent-owned. _ensure_agent_owned is mocked so we assert the CALL sites.
    svc = _service()
    wt = clone / ".worktrees" / "a3c40fe7"
    owned: list[Path] = []

    def _capture(p: Path) -> None:
        owned.append(p)

    with patch("roboco.services.workspace._ensure_agent_owned", side_effect=_capture):
        await svc.ensure_worktree(clone, wt, "feature/a3c40fe7", "main")

    assert clone in owned, "clone root must be chowned (shared .venv/.git)"
    assert wt in owned, "worktree working tree must be chowned"


async def test_ensure_worktree_for_resume_noop_when_present(clone: Path) -> None:
    svc = _service()
    wt = clone / ".worktrees" / "a3c40fe7"

    with patch("roboco.services.workspace._ensure_agent_owned"):
        await svc.ensure_worktree(clone, wt, "feature/a3c40fe7", "main")
        # Resume on an existing worktree: no-op, branch intact.
        await svc.ensure_worktree_for_resume(clone, wt, "feature/a3c40fe7")

    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == "feature/a3c40fe7"


async def test_ensure_worktree_for_resume_readds_pruned_worktree(clone: Path) -> None:
    # A pruned/evicted worktree must be re-added on resume (committed work
    # survives in the branch ref). Re-add uses NO -b (branch already exists).
    svc = _service()
    wt = clone / ".worktrees" / "a3c40fe7"

    with patch("roboco.services.workspace._ensure_agent_owned"):
        await svc.ensure_worktree(clone, wt, "feature/a3c40fe7", "main")
        # Simulate eviction: remove the worktree out-of-band.
        _git(clone, "worktree", "remove", str(wt), "--force")
    assert not wt.exists()

    with patch("roboco.services.workspace._ensure_agent_owned"):
        await svc.ensure_worktree_for_resume(clone, wt, "feature/a3c40fe7")

    assert wt.exists()
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == "feature/a3c40fe7"


async def test_remove_worktree_cleans_up_and_prunes(clone: Path) -> None:
    svc = _service()
    wt = clone / ".worktrees" / "a3c40fe7"

    with patch("roboco.services.workspace._ensure_agent_owned"):
        await svc.ensure_worktree(clone, wt, "feature/a3c40fe7", "main")
        await svc.remove_worktree(clone, wt)

    assert not wt.exists(), "worktree dir must be gone"
    listed = _git(clone, "worktree", "list", "--porcelain")
    assert str(wt) not in listed, "worktree must be unregistered from clone"


async def test_remove_worktree_noop_on_missing_worktree(clone: Path) -> None:
    # Cancel/reaper on a task whose worktree was never created (or already
    # removed) must not raise.
    svc = _service()
    wt = clone / ".worktrees" / "never"
    await svc.remove_worktree(clone, wt)  # no error
    assert not wt.exists()


async def test_two_concurrent_task_worktrees_independent(clone: Path) -> None:
    # THE F123 assertion: two tasks of one PM get independent checkouts on the
    # same clone, each on its own branch, neither clobbering the other.
    svc = _service()
    wt_a = clone / ".worktrees" / "a3c40fe7"
    wt_b = clone / ".worktrees" / "8e460893"

    with patch("roboco.services.workspace._ensure_agent_owned"):
        await svc.ensure_worktree(clone, wt_a, "feature/a3c40fe7", "main")
        await svc.ensure_worktree(clone, wt_b, "feature/8e460893", "main")

    # Edit in worktree A does not appear in worktree B.
    (wt_a / "new.txt").write_text("a")
    assert (wt_a / "new.txt").exists()
    assert not (wt_b / "new.txt").exists()
    assert _git(wt_a, "rev-parse", "--abbrev-ref", "HEAD").strip() == "feature/a3c40fe7"
    assert _git(wt_b, "rev-parse", "--abbrev-ref", "HEAD").strip() == "feature/8e460893"
    # Clone root stays on main — neither task branch moved it.
    assert _git(clone, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"
