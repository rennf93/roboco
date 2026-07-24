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


async def test_self_heal_present_worktree_fetches_but_noops_without_origin(
    clone: Path,
) -> None:
    # A present worktree is no longer an unconditional no-op (the respawn
    # bug): a fetch is now always attempted. This `clone` fixture carries no
    # `origin` remote at all, so `origin/<branch>` can never resolve and the
    # refresh has nothing to compare against — same observable outcome as
    # the old no-op, but for a different reason (unresolvable, not skipped).
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
        await svc.ensure_worktree_self_heal(
            clone, wt, "feature/a3c40fe7", "proj", can_author=True
        )

    assert fetch.await_count == 1, "present worktree must now attempt a fetch"
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == "feature/a3c40fe7"


async def test_self_heal_readds_pruned_worktree_from_local_ref(clone: Path) -> None:
    # Common resume case: clone healthy, worktree pruned, local branch ref
    # survives -> re-add, THEN run it through the same fetch-and-classify
    # refresh a present worktree gets (a stale local ref must not resurrect an
    # untouched checkout). This `clone` fixture carries no `origin` remote, so
    # there is nothing to classify against — the refresh's own fetch still
    # runs, it just has no origin/<branch> to compare to.
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
        # can_author is irrelevant on the absent-worktree path (pre-refresh).
        await svc.ensure_worktree_self_heal(
            clone, wt, "feature/a3c40fe7", "proj", can_author=True
        )

    assert fetch.await_count == 1, (
        "a re-add from a surviving local ref must now refresh"
    )
    assert wt.exists()
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == "feature/a3c40fe7"


async def test_self_heal_readd_from_stale_local_ref_lands_on_origin_tip_for_reader(
    tmp_path: Path,
) -> None:
    # THE BUG SCENARIO: a reviewer's round-1 claim_review creates the worktree
    # + local ref at origin's tip A; the worktree is evicted (disk pressure /
    # manual cleanup) while the local ref survives; a dev then pushes tip B.
    # A round-2 respawn's re-add must land on B, not resurrect the stale local
    # ref's A.
    branch = "feature/pruned-stale-ref"
    remote = _bare_remote_with_branch(tmp_path, branch, push_branch=False)
    clone = await _synced_clone_and_worktree(tmp_path, remote, branch)
    wt = clone / ".worktrees" / "pruned-stale-ref"
    _git(clone, "worktree", "remove", str(wt), "--force")  # evicted; local ref survives
    assert not wt.exists()
    assert _ref_exists(clone, f"refs/heads/{branch}"), (
        "precondition: local ref survives"
    )
    _push_extra_commit(tmp_path, remote, branch, "other")  # origin advances to tip B
    _git(clone, "fetch", "origin", branch)  # what the mocked _fetch_branch_ref would do

    svc = _service()
    await _run_self_heal(svc, clone, wt, branch, can_author=False)

    assert wt.exists()
    assert (wt / "origin_advance.txt").exists(), (
        "a re-add from a stale local ref must land on origin's tip, not the "
        "ref's stale commit"
    )
    assert (
        _git(wt, "rev-parse", "HEAD").strip()
        == _git(clone, "rev-parse", f"origin/{branch}").strip()
    )


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
        # can_author is irrelevant on the absent-worktree path (pre-refresh).
        await svc.ensure_worktree_self_heal(clone, wt, branch, "proj", can_author=True)

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
        # can_author is irrelevant on the absent-worktree path (pre-refresh).
        await svc.ensure_worktree_self_heal(clone, wt, branch, "proj", can_author=True)

    assert fetch.await_count == 1, "missing local ref still attempts a fetch"
    assert wt.exists(), "fallback -b from origin/HEAD must break the loop"
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == branch


# ---------------------------------------------------------------------------
# _refresh_present_worktree — role-aware respawn refresh of an ALREADY-PRESENT
# worktree (the respawn bug). A worktree created once (first claim / first
# claim_review) must not stay frozen at that commit across every later
# respawn while origin moves on. `_fetch_branch_ref` is mocked (a spy, as
# above) — the tests pre-seed `origin/<branch>`'s remote-tracking ref with a
# real `git fetch` so the classification runs against real git state.
# ---------------------------------------------------------------------------


async def _synced_clone_and_worktree(tmp_path: Path, remote: Path, branch: str) -> Path:
    """Clone `remote`, create+push a worktree on `branch` at origin's tip.

    Mirrors `create_branch`'s real shape: the worktree branch is cut, then
    pushed, so local and `origin/<branch>` start perfectly in sync.
    """
    clone = tmp_path / "clone"
    subprocess.run(
        ["git", "clone", str(remote), str(clone)], check=True, capture_output=True
    )
    svc = _service()
    wt = clone / ".worktrees" / branch.rsplit("/", 1)[-1]
    with patch("roboco.services.workspace._ensure_agent_owned"):
        await svc.ensure_worktree(clone, wt, branch, "main")
    _git(wt, "push", "origin", branch)
    return clone


def _push_extra_commit(tmp_path: Path, remote: Path, branch: str, name: str) -> None:
    """A second clone pushes one more commit onto `branch` (simulates a dev's
    force-pushed fix landing on origin between two reviewer respawns)."""
    other = tmp_path / name
    subprocess.run(
        ["git", "clone", str(remote), str(other)], check=True, capture_output=True
    )
    _git(other, "checkout", branch)
    (other / "origin_advance.txt").write_text(name)
    _git(other, "add", "origin_advance.txt")
    _git(other, "commit", "-m", "origin advances")
    _git(other, "push", "origin", branch)


def _commit_local_only(wt: Path) -> None:
    """A commit in the worktree that never reaches origin (unpushed work)."""
    (wt / "local_only.txt").write_text("mine")
    _git(wt, "add", "local_only.txt")
    _git(wt, "commit", "-m", "local unpushed work")


async def _run_self_heal(
    svc: WorkspaceService, clone: Path, wt: Path, branch: str, *, can_author: bool
) -> None:
    with (
        patch.object(WorkspaceService, "_fetch_branch_ref", new_callable=AsyncMock),
        patch("roboco.services.workspace._ensure_agent_owned"),
    ):
        await svc.ensure_worktree_self_heal(
            clone, wt, branch, "proj", can_author=can_author
        )


async def test_refresh_reader_diverged_resets_to_origin(tmp_path: Path) -> None:
    branch = "feature/reader-diverged"
    remote = _bare_remote_with_branch(tmp_path, branch, push_branch=False)
    clone = await _synced_clone_and_worktree(tmp_path, remote, branch)
    wt = clone / ".worktrees" / "reader-diverged"
    _commit_local_only(wt)  # local ref now ahead of origin/<branch>
    _push_extra_commit(tmp_path, remote, branch, "other")  # ...and origin too
    _git(clone, "fetch", "origin", branch)  # what the mocked _fetch_branch_ref would do
    assert (wt / "local_only.txt").exists(), "precondition: local commit present"

    svc = _service()
    await _run_self_heal(svc, clone, wt, branch, can_author=False)

    assert not (wt / "local_only.txt").exists(), (
        "reader's diverged local history is disposable — must reset to origin"
    )
    assert (wt / "origin_advance.txt").exists(), "origin's tip must now be checked out"
    assert (
        _git(wt, "rev-parse", "HEAD").strip()
        == _git(clone, "rev-parse", f"origin/{branch}").strip()
    )


async def test_refresh_author_ahead_untouched(tmp_path: Path) -> None:
    branch = "feature/author-ahead"
    remote = _bare_remote_with_branch(tmp_path, branch, push_branch=False)
    clone = await _synced_clone_and_worktree(tmp_path, remote, branch)
    wt = clone / ".worktrees" / "author-ahead"
    _commit_local_only(wt)  # strictly ahead — origin never moved
    _git(clone, "fetch", "origin", branch)

    svc = _service()
    await _run_self_heal(svc, clone, wt, branch, can_author=True)

    assert (wt / "local_only.txt").exists(), "strictly-ahead work is never discarded"


async def test_refresh_author_diverged_untouched(tmp_path: Path) -> None:
    branch = "feature/author-diverged"
    remote = _bare_remote_with_branch(tmp_path, branch, push_branch=False)
    clone = await _synced_clone_and_worktree(tmp_path, remote, branch)
    wt = clone / ".worktrees" / "author-diverged"
    _commit_local_only(wt)
    _push_extra_commit(tmp_path, remote, branch, "other")
    _git(clone, "fetch", "origin", branch)

    svc = _service()
    await _run_self_heal(svc, clone, wt, branch, can_author=True)

    assert (wt / "local_only.txt").exists(), (
        "an author's diverged history is sync_branch's job, never a silent reset"
    )
    assert not (wt / "origin_advance.txt").exists(), "no reset must have run at all"


async def test_refresh_behind_fast_forwards_for_any_role(tmp_path: Path) -> None:
    branch = "feature/behind"
    remote = _bare_remote_with_branch(tmp_path, branch, push_branch=False)
    clone = await _synced_clone_and_worktree(tmp_path, remote, branch)
    wt = clone / ".worktrees" / "behind"
    _push_extra_commit(tmp_path, remote, branch, "other")  # local has nothing unique
    _git(clone, "fetch", "origin", branch)

    svc = _service()
    await _run_self_heal(svc, clone, wt, branch, can_author=True)

    assert (wt / "origin_advance.txt").exists(), (
        "behind-or-equal is safe to fast-forward for every role"
    )


async def test_refresh_dirty_author_tree_preserved_even_when_behind(
    tmp_path: Path,
) -> None:
    branch = "feature/dirty-author"
    remote = _bare_remote_with_branch(tmp_path, branch, push_branch=False)
    clone = await _synced_clone_and_worktree(tmp_path, remote, branch)
    wt = clone / ".worktrees" / "dirty-author"
    (wt / "pyproject.toml").write_text("[project]\nname = 'edited'\n")  # uncommitted
    _push_extra_commit(tmp_path, remote, branch, "other")
    _git(clone, "fetch", "origin", branch)

    svc = _service()
    await _run_self_heal(svc, clone, wt, branch, can_author=True)

    assert (wt / "pyproject.toml").read_text() == "[project]\nname = 'edited'\n", (
        "an author's uncommitted edit must never be discarded by a reset"
    )
    assert not (wt / "origin_advance.txt").exists(), "no reset must have run at all"


def test_worktree_is_dirty_treats_failed_status_as_dirty(tmp_path: Path) -> None:
    # A failed `git status` (nonzero returncode, empty stdout) must read as
    # dirty, never clean — a false "clean" here lets an author+behind branch
    # proceed straight to `reset --hard` and discard uncommitted edits.
    failed = subprocess.CompletedProcess(
        args=[], returncode=128, stdout="", stderr="fatal: not a git repository"
    )
    with patch.object(WorkspaceService, "_worktree_git", return_value=failed):
        assert WorkspaceService._worktree_is_dirty(tmp_path) is True


async def test_refresh_skips_reset_when_worktree_drifted_off_task_branch(
    tmp_path: Path,
) -> None:
    # A worktree parked on some OTHER branch (a crashed mid-rebase, a drifted
    # checkout) must never have the task branch's ref reset under it — a
    # `reset --hard` runs in the worktree's own checked-out branch, not
    # necessarily the task branch, so blindly resetting would move the wrong
    # ref.
    branch = "feature/drifted"
    remote = _bare_remote_with_branch(tmp_path, branch, push_branch=False)
    clone = await _synced_clone_and_worktree(tmp_path, remote, branch)
    wt = clone / ".worktrees" / "drifted"
    _push_extra_commit(tmp_path, remote, branch, "other")  # task branch now behind
    _git(clone, "fetch", "origin", branch)
    _git(wt, "checkout", "-b", "other-work")  # worktree drifts off the task branch

    svc = _service()
    with patch("roboco.services.workspace.logger.warning") as warn:
        await _run_self_heal(svc, clone, wt, branch, can_author=True)

    assert warn.called, "a drifted worktree must log a warning instead of resetting"
    assert not (wt / "origin_advance.txt").exists(), (
        "a worktree drifted off its task branch must be left alone"
    )
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == "other-work"


# ---------------------------------------------------------------------------
# Clone-root-left-on-task-branch recovery (live be-pm needs_revision wedge,
# 2026-06-30). F123 invariant: the clone root parks on the default branch (or
# detached); the task branch lives in the worktree. A re-dispatch after the clone
# root drifted onto the task branch (a pre-F123 leftover / missed checkout)
# made `git worktree add <branch>` fatal ("already checked out at '<clone>'"),
# releasing the claim and re-dispatching into the same collision every tick.
# ensure_worktree must restore the invariant before the add: move the clone root
# back to the default branch (via origin/HEAD), detaching as a fallback so the
# branch ref is free for the worktree either way.
# ---------------------------------------------------------------------------


def _clone_on_branch(clone: Path, branch: str) -> None:
    _git(clone, "branch", branch)
    _git(clone, "checkout", branch)


async def test_ensure_worktree_restores_clone_root_left_on_task_branch(
    clone: Path,
) -> None:
    svc = _service()
    branch = "feature/d3dab0fc"
    # Set up a resolvable origin/HEAD (a real clone has this) so the default
    # branch is "main", then drift the clone root onto the task branch.
    main_sha = _git(clone, "rev-parse", "main").strip()
    _git(clone, "remote", "add", "origin", str(clone))
    _git(clone, "update-ref", "refs/remotes/origin/main", main_sha)
    _git(clone, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    _clone_on_branch(clone, branch)
    assert _git(clone, "branch", "--show-current").strip() == branch

    wt = clone / ".worktrees" / "d3dab0fc"
    with patch("roboco.services.workspace._ensure_agent_owned"):
        await svc.ensure_worktree(clone, wt, branch, "origin/HEAD")

    assert (wt / ".git").is_file(), "worktree must be created despite the collision"
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == branch
    # Clone root restored to the default branch (F123 invariant), not still on
    # the task branch, and not left dangling mid-recovery.
    assert _git(clone, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"


async def test_ensure_worktree_detaches_when_default_branch_unresolvable(
    clone: Path,
) -> None:
    # No origin remote (e.g. a test/local clone): origin/HEAD can't resolve, so
    # the recovery detaches the clone root to free the branch for the worktree.
    svc = _service()
    branch = "feature/d3dab0fc"
    _clone_on_branch(clone, branch)
    assert _git(clone, "branch", "--show-current").strip() == branch

    wt = clone / ".worktrees" / "d3dab0fc"
    with patch("roboco.services.workspace._ensure_agent_owned"):
        await svc.ensure_worktree(clone, wt, branch, "origin/HEAD")

    assert (wt / ".git").is_file(), "worktree must be created (branch freed via detach)"
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == branch
    # Detached HEAD (abbrev-ref is HEAD), the task branch ref no longer checked
    # out at the clone root.
    assert _git(clone, "rev-parse", "--abbrev-ref", "HEAD").strip() == "HEAD"
    assert _ref_exists(clone, f"refs/heads/{branch}"), "branch ref preserved"


async def test_self_heal_recovers_clone_root_left_on_task_branch(clone: Path) -> None:
    # The live failure path: spawn -> ensure_worktree_self_heal -> worktree add.
    # With the clone root parked on the task branch, the self-heal re-add must
    # restore the invariant and re-attach the worktree instead of fatal-looping.
    svc = _service()
    branch = "feature/d3dab0fc"
    main_sha = _git(clone, "rev-parse", "main").strip()
    _git(clone, "remote", "add", "origin", str(clone))
    _git(clone, "update-ref", "refs/remotes/origin/main", main_sha)
    _git(clone, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")
    _clone_on_branch(clone, branch)

    wt = clone / ".worktrees" / "d3dab0fc"
    with (
        patch.object(WorkspaceService, "_fetch_branch_ref", new_callable=AsyncMock),
        patch("roboco.services.workspace._ensure_agent_owned"),
    ):
        # The local ref here comes straight from `_clone_on_branch`, not a
        # prior `ensure_worktree` — a surviving local ref now also re-adds
        # through the present-worktree refresh; origin/<branch> is
        # unresolvable (only origin/main was seeded), so the refresh's fetch
        # is a no-op past the re-add.
        await svc.ensure_worktree_self_heal(clone, wt, branch, "proj", can_author=True)

    assert (wt / ".git").is_file()
    assert _git(wt, "rev-parse", "--abbrev-ref", "HEAD").strip() == branch
    assert _git(clone, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"


# ---------------------------------------------------------------------------
# delete_local_branch — cleans up the branch ref remove_worktree leaves behind
# (worktree removal never deletes refs/heads/{branch}; every claimed task
# leaked a permanent local branch ref until this).
# ---------------------------------------------------------------------------


async def test_delete_local_branch_skips_default_branches(clone: Path) -> None:
    svc = _service()
    for branch in ("main", "master", "develop", ""):
        await svc.delete_local_branch(clone, branch, force=True)
    # "main" is the clone's actual current branch — still checked out, untouched.
    assert _git(clone, "rev-parse", "--abbrev-ref", "HEAD").strip() == "main"


async def test_delete_local_branch_removes_merged_branch(clone: Path) -> None:
    svc = _service()
    _git(clone, "branch", "feature/merged")  # no new commits -> already merged
    assert _ref_exists(clone, "refs/heads/feature/merged")

    await svc.delete_local_branch(clone, "feature/merged", force=False)

    assert not _ref_exists(clone, "refs/heads/feature/merged")


async def test_delete_local_branch_not_merged_skips_without_force(clone: Path) -> None:
    svc = _service()
    _git(clone, "checkout", "-b", "feature/unmerged")
    (clone / "work.txt").write_text("x")
    _git(clone, "add", "work.txt")
    _git(clone, "commit", "-m", "unmerged work")
    _git(clone, "checkout", "main")
    assert _ref_exists(clone, "refs/heads/feature/unmerged")

    await svc.delete_local_branch(clone, "feature/unmerged", force=False)

    # `-d` refuses an unmerged branch — a clean skip, not an error, ref stays.
    assert _ref_exists(clone, "refs/heads/feature/unmerged")


async def test_delete_local_branch_force_deletes_unmerged(clone: Path) -> None:
    svc = _service()
    _git(clone, "checkout", "-b", "feature/unmerged")
    (clone / "work.txt").write_text("x")
    _git(clone, "add", "work.txt")
    _git(clone, "commit", "-m", "unmerged work")
    _git(clone, "checkout", "main")
    assert _ref_exists(clone, "refs/heads/feature/unmerged")

    await svc.delete_local_branch(clone, "feature/unmerged", force=True)

    assert not _ref_exists(clone, "refs/heads/feature/unmerged")


async def test_delete_local_branch_squash_merged_needs_force(clone: Path) -> None:
    # RoboCo's default merge method is SQUASH: the branch's commits are never
    # ancestors of the base afterwards, so `-d` refuses even though the work
    # fully landed. This is why every terminal-path caller passes force=True —
    # with -d the completed-task ref would leak forever.
    svc = _service()
    _git(clone, "checkout", "-b", "feature/squashed")
    (clone / "work.txt").write_text("x")
    _git(clone, "add", "work.txt")
    _git(clone, "commit", "-m", "feature work")
    _git(clone, "checkout", "main")
    _git(clone, "merge", "--squash", "feature/squashed")
    _git(clone, "commit", "-m", "squash-merge feature")
    assert (clone / "work.txt").exists()  # the work landed on main

    await svc.delete_local_branch(clone, "feature/squashed", force=False)
    assert _ref_exists(clone, "refs/heads/feature/squashed")  # -d refused

    await svc.delete_local_branch(clone, "feature/squashed", force=True)
    assert not _ref_exists(clone, "refs/heads/feature/squashed")


async def test_delete_local_branch_missing_branch_is_clean_skip(clone: Path) -> None:
    svc = _service()
    await svc.delete_local_branch(
        clone, "feature/never-existed", force=False
    )  # no error
    await svc.delete_local_branch(
        clone, "feature/never-existed", force=True
    )  # no error
