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
from unittest.mock import AsyncMock, patch

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


# ---------------------------------------------------------------------------
# ensure_worktree_self_heal — spawn-time clone + branch-ref self-heal (F123).
# A vanished clone_root fatal-looped the resume path (`git -C <missing>`); the
# reaper-style claim release preserves ownership + branch_name, so the next
# dispatch is a RESUME (create_branch never re-runs to re-clone). self_heal
# recovers the branch ref from origin (create_branch pushes at claim time) so
# the pushed work survives, falling back to a fresh branch off origin/HEAD
# only when the branch was never pushed.
# ---------------------------------------------------------------------------


def _ref_exists(repo: Path, ref: str) -> bool:
    return (
        subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--verify", "--quiet", ref],
            check=False,
        ).returncode
        == 0
    )


async def test_self_heal_noop_when_worktree_present(clone: Path) -> None:
    svc = _service()
    wt = clone / ".worktrees" / "a3c40fe7"
    with patch("roboco.services.workspace._ensure_agent_owned"):
        await svc.ensure_worktree(clone, wt, "feature/a3c40fe7", "main")

    with (
        patch.object(
            WorkspaceService, "_fetch_branch_ref", new_callable=AsyncMock
        ) as fetch,
        patch("roboco.services.workspace._ensure_agent_owned"),
    ):
        await svc.ensure_worktree_self_heal(clone, wt, "feature/a3c40fe7", "proj")

    assert fetch.await_count == 0, "present worktree must not trigger a fetch"
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == "feature/a3c40fe7"


async def test_self_heal_readds_pruned_worktree_from_local_ref(clone: Path) -> None:
    # Common resume case: clone healthy, worktree pruned, local branch ref
    # survives -> re-add with NO fetch (no origin round-trip on every spawn).
    svc = _service()
    wt = clone / ".worktrees" / "a3c40fe7"
    with patch("roboco.services.workspace._ensure_agent_owned"):
        await svc.ensure_worktree(clone, wt, "feature/a3c40fe7", "main")
    _git(clone, "worktree", "remove", str(wt), "--force")  # prune
    assert not wt.exists()

    with (
        patch.object(
            WorkspaceService, "_fetch_branch_ref", new_callable=AsyncMock
        ) as fetch,
        patch("roboco.services.workspace._ensure_agent_owned"),
    ):
        await svc.ensure_worktree_self_heal(clone, wt, "feature/a3c40fe7", "proj")

    assert fetch.await_count == 0, "local ref survives -> no fetch needed"
    assert wt.exists()
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == "feature/a3c40fe7"


def _bare_remote_with_branch(tmp_path: Path, branch: str, push_branch: bool) -> Path:
    """A bare remote carrying `main`; optionally also `branch` with a commit."""
    remote = tmp_path / "remote.git"
    remote.mkdir()
    _git(remote, "init", "--bare", "-b", "main")
    src = tmp_path / "src"
    _init_clone(src)
    _git(src, "remote", "add", "origin", str(remote))
    _git(src, "push", "origin", "main")
    if push_branch:
        _git(src, "checkout", "-b", branch)
        (src / "work.txt").write_text("x")
        _git(src, "add", "work.txt")
        _git(src, "commit", "-m", "work")
        _git(src, "push", "origin", branch)
    return remote


def _recloned_clone(
    tmp_path: Path, remote: Path, fetch_branch: str | None = None
) -> Path:
    """A clone with only `main` locally (simulates a fresh re-clone: no task
    branch ref). origin/HEAD is set so ensure_worktree's -b fallback resolves.
    When ``fetch_branch`` is given, its remote-tracking ref is pre-seeded here
    (the real ``_fetch_branch_ref`` is mocked in the test) to model a branch
    that was pushed at claim time and is recoverable from origin."""
    clone = tmp_path / "clone"
    _init_clone(clone)
    _git(clone, "remote", "add", "origin", str(remote))
    _git(clone, "fetch", "origin", "main")
    if fetch_branch is not None:
        _git(clone, "fetch", "origin", fetch_branch)
    _git(clone, "remote", "set-head", "origin", "main")
    return clone


async def test_self_heal_recovers_branch_from_origin(tmp_path: Path) -> None:
    # THE BUG SCENARIO: clone vanished, re-cloned (only main locally), but the
    # task branch was pushed at claim time -> recover it from origin so the
    # pushed work survives (not -b'd over with a divergent branch). The real
    # _fetch_branch_ref is mocked (a spy); the remote-tracking ref it would
    # populate is pre-seeded, exercising the ref-recovery + worktree re-add.
    branch = "feature/8e460893"
    remote = _bare_remote_with_branch(tmp_path, branch, push_branch=True)
    clone = _recloned_clone(tmp_path, remote, fetch_branch=branch)
    assert not _ref_exists(clone, f"refs/heads/{branch}"), "precondition: no local ref"
    assert _ref_exists(clone, f"refs/remotes/origin/{branch}"), (
        "precondition: pushed branch reachable on origin"
    )

    svc = _service()
    wt = clone / ".worktrees" / "8e460893"

    with (
        patch.object(
            WorkspaceService, "_fetch_branch_ref", new_callable=AsyncMock
        ) as fetch,
        patch("roboco.services.workspace._ensure_agent_owned"),
    ):
        await svc.ensure_worktree_self_heal(clone, wt, branch, "proj")

    assert fetch.await_count == 1, "missing local ref must trigger a fetch"
    assert wt.exists()
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == branch
    # Recovered commit present — pushed work was NOT lost to a divergent -b.
    assert (wt / "work.txt").exists(), "pushed commit must survive recovery"


async def test_self_heal_falls_back_to_origin_head_when_branch_not_pushed(
    tmp_path: Path,
) -> None:
    # Never-pushed branch (push failed at claim time, or first claim never
    # pushed): origin doesn't have it -> re-create from origin/HEAD rather
    # than fatal-loop. No pushed work is lost because none existed.
    branch = "feature/8e460893"
    remote = _bare_remote_with_branch(tmp_path, branch, push_branch=False)
    clone = _recloned_clone(tmp_path, remote)
    assert not _ref_exists(clone, f"refs/remotes/origin/{branch}"), (
        "precondition: not on origin"
    )

    svc = _service()
    wt = clone / ".worktrees" / "8e460893"

    with (
        patch.object(
            WorkspaceService, "_fetch_branch_ref", new_callable=AsyncMock
        ) as fetch,
        patch("roboco.services.workspace._ensure_agent_owned"),
    ):
        await svc.ensure_worktree_self_heal(clone, wt, branch, "proj")

    assert fetch.await_count == 1, "missing local ref still attempts a fetch"
    assert wt.exists(), "fallback -b from origin/HEAD must break the loop"
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == branch
