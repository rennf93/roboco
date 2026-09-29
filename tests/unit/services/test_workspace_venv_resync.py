"""Staged-venv re-sync on worktree lockfile divergence.

The shared clone-root dev venv was provisioned once at clone time and never
re-synced: the install marker recorded the digest of the PARKED clone root's
lockfiles, so a task worktree carrying a newer uv.lock kept running against
clone-time pins (the fastapi-guard 7.3.1 vs 7.8.2 incident). The fix derives
the digest from the worktree's lockfiles and, on mismatch, builds a fresh
venv at a digest-named staged path and atomically flips the shared .venv
symlink, retaining the previous venv so a concurrently active sibling
worktree is never broken mid-task.

These tests use fakes for the install subprocess — no network, no real uv.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from roboco.services.workspace import (
    _DEP_INSTALL_MARKER,
    WorkspaceService,
    _lockfile_digest,
)


def _service() -> WorkspaceService:
    """Build a WorkspaceService over a MagicMock session."""
    session = MagicMock()
    return WorkspaceService(session)


def _clone_root(tmp_path: Path) -> Path:
    root = tmp_path / "roboco" / "backend" / "be-dev-1"
    (root / ".git").mkdir(parents=True)
    return root


def _worktree(clone_root: Path, name: str, lockfile: str) -> Path:
    worktree = clone_root / ".worktrees" / name
    worktree.mkdir(parents=True)
    (worktree / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (worktree / "uv.lock").write_text(lockfile)
    # Worktree gitlink (a file, like a real linked worktree).
    (worktree / ".git").write_text("gitdir: ../../.git/worktrees/x\n")
    return worktree


def _fake_install_recorder(venv_contents: str = "dev-toolchain") -> AsyncMock:
    """Async side effect replacing _run_dep_install: records calls and
    'builds' the staged venv the override points at."""

    async def _install(
        _workspace: Path,
        _label: str,
        _argv: list[str],
        env_overrides: dict[str, str] | None = None,
    ) -> bool:
        if env_overrides and "UV_PROJECT_ENVIRONMENT" in env_overrides:
            staged = Path(env_overrides["UV_PROJECT_ENVIRONMENT"])
            (staged / "bin").mkdir(parents=True, exist_ok=True)
            (staged / "bin" / "python").write_text(venv_contents)
        return True

    return AsyncMock(side_effect=_install)


async def test_lockfile_change_reruns_install_and_updates_marker(
    tmp_path: Path,
) -> None:
    """Regression pin: a worktree carrying a changed lockfile re-runs the
    install (the shared-venv marker digest changes to the worktree's)."""
    root = _clone_root(tmp_path)
    # Legacy layout: real .venv dir, marker frozen at a clone-time digest.
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / ".venv" / "bin" / "python").write_text("old")
    marker = root / _DEP_INSTALL_MARKER
    marker.write_text("clone-time-frozen-digest")

    worktree = _worktree(root, "t1", 'version = "1"\n')
    new_digest = _lockfile_digest(worktree)
    assert new_digest is not None

    svc = _service()
    recorder = _fake_install_recorder()
    with patch.object(WorkspaceService, "_run_dep_install", recorder):
        ran = await svc.ensure_venv_matches_worktree(root, worktree)

    assert ran is True
    assert marker.read_text() == new_digest
    # Install ran once, from the WORKTREE, into a staged path.
    assert recorder.await_count == 1
    call = recorder.await_args
    assert call is not None
    kwargs = call.kwargs
    assert kwargs["env_overrides"] is not None
    staged = Path(kwargs["env_overrides"]["UV_PROJECT_ENVIRONMENT"])
    assert staged.parent == root
    assert staged.name.startswith(".venv-staged-")
    assert call.args[1] == "uv sync --extra dev"
    # Shared .venv is now a symlink onto the staged venv.
    venv = root / ".venv"
    assert venv.is_symlink()
    assert Path(venv.parent / venv.readlink()) == staged
    # Legacy venv retained (running interpreters hold handles into it).
    assert (staged / "bin" / "python").read_text() == "dev-toolchain"
    prev = list(root.glob(".venv-prev-*"))
    assert len(prev) == 1
    assert (prev[0] / "bin" / "python").read_text() == "old"


async def test_same_digest_is_a_noop(tmp_path: Path) -> None:
    """Anti-thrash: a re-entry with the same lockfiles installs nothing."""
    root = _clone_root(tmp_path)
    worktree = _worktree(root, "t1", 'version = "1"\n')
    digest = _lockfile_digest(worktree)
    assert digest is not None
    (root / _DEP_INSTALL_MARKER).write_text(digest)

    svc = _service()
    recorder = _fake_install_recorder()
    with patch.object(WorkspaceService, "_run_dep_install", recorder):
        ran = await svc.ensure_venv_matches_worktree(root, worktree)

    assert ran is False
    recorder.assert_not_awaited()


async def test_bumped_lockfile_installs_newly_pinned_version(
    tmp_path: Path,
) -> None:
    """Pinned-version pin: after a uv.lock bump, the re-sync builds the venv
    for the NEW lockfile — the staged venv 'contains' the bumped pin."""
    root = _clone_root(tmp_path)
    old_worktree = _worktree(root, "t1", 'name = "fastapi-guard"\nversion = "7.3.1"\n')
    old_digest = _lockfile_digest(old_worktree)
    assert old_digest is not None
    marker = root / _DEP_INSTALL_MARKER
    marker.write_text(old_digest)
    # Current shared-venv layout: symlink onto the old staged dir.
    old_staged = root / f".venv-staged-{old_digest[:12]}"
    (old_staged / "bin").mkdir(parents=True)
    (old_staged / "bin" / "python").write_text("fastapi-guard==7.3.1")
    (root / ".venv").symlink_to(old_staged.name)

    bumped = _worktree(root, "t2", 'name = "fastapi-guard"\nversion = "7.8.2"\n')
    new_digest = _lockfile_digest(bumped)
    assert new_digest != old_digest

    svc = _service()
    recorder = _fake_install_recorder(venv_contents="fastapi-guard==7.8.2")
    with patch.object(WorkspaceService, "_run_dep_install", recorder):
        ran = await svc.ensure_venv_matches_worktree(root, bumped)

    assert ran is True
    assert marker.read_text() == new_digest
    venv = root / ".venv"
    staged = Path(venv.parent / venv.readlink())
    assert staged.name == f".venv-staged-{new_digest[:12]}"
    assert (staged / "bin" / "python").read_text() == "fastapi-guard==7.8.2"
    # The old venv dir survives the flip.
    assert (old_staged / "bin" / "python").read_text() == "fastapi-guard==7.3.1"


async def test_stale_marker_format_counts_as_mismatch(tmp_path: Path) -> None:
    """A legacy/old-format marker content is a mismatch: exactly one
    re-sync fires on the first ensure after this ships."""
    root = _clone_root(tmp_path)
    (root / ".venv").mkdir()
    (root / _DEP_INSTALL_MARKER).write_text("v1:some-old-format")
    worktree = _worktree(root, "t1", 'version = "1"\n')

    svc = _service()
    recorder = _fake_install_recorder()
    with patch.object(WorkspaceService, "_run_dep_install", recorder):
        ran = await svc.ensure_venv_matches_worktree(root, worktree)
        ran_again = await svc.ensure_venv_matches_worktree(root, worktree)

    assert ran is True
    assert ran_again is False
    assert recorder.await_count == 1


async def test_active_sibling_survives_staged_swap(tmp_path: Path) -> None:
    """Concurrency safety: a sibling worktree mid-task keeps a valid venv
    path/interpreter across another worktree's re-sync flip."""
    root = _clone_root(tmp_path)
    old_worktree = _worktree(root, "t1", 'version = "1"\n')
    old_digest = _lockfile_digest(old_worktree)
    assert old_digest is not None
    old_staged = root / f".venv-staged-{old_digest[:12]}"
    (old_staged / "bin").mkdir(parents=True)
    (old_staged / "bin" / "python").write_text("old interpreter")
    (root / ".venv").symlink_to(old_staged.name)
    (root / _DEP_INSTALL_MARKER).write_text(old_digest)

    # Sibling worktree, actively linked to the shared venv.
    sibling = _worktree(root, "sibling", 'version = "1"\n')
    sibling_venv = sibling / ".venv"
    sibling_venv.symlink_to("../../.venv")
    sibling_python = sibling_venv / "bin" / "python"
    assert sibling_python.is_file()  # valid before the swap

    bumped = _worktree(root, "t2", 'version = "2"\n')
    svc = _service()
    recorder = _fake_install_recorder(venv_contents="new interpreter")
    with patch.object(WorkspaceService, "_run_dep_install", recorder):
        ran = await svc.ensure_venv_matches_worktree(root, bumped)

    assert ran is True
    # The shared symlink flipped onto the new staged venv...
    new_target = Path(root / ".venv").parent / (root / ".venv").readlink()
    assert new_target != old_staged
    assert (new_target / "bin" / "python").read_text() == "new interpreter"
    # ...and the sibling's path still resolves to a live interpreter (its
    # symlink chases the flip), while the OLD venv dir is retained for any
    # already-running process holding open handles into it.
    assert sibling_python.is_file()
    assert old_staged.exists()
    assert (old_staged / "bin" / "python").read_text() == "old interpreter"


async def test_ensure_worktree_invokes_the_resync_hook(tmp_path: Path) -> None:
    """ensure_worktree (the fresh-claim chokepoint) runs the re-sync after
    linking the shared venv."""
    root = _clone_root(tmp_path)
    worktree = _worktree(root, "t1", 'version = "1"\n')
    # A shared venv must exist for _link_shared_venv to link.
    (root / ".venv").mkdir()

    svc = _service()
    ok = MagicMock(returncode=0, stdout="", stderr="")
    resync = AsyncMock(return_value=False)
    with (
        patch.object(WorkspaceService, "_worktree_git", ok),
        patch.object(WorkspaceService, "ensure_venv_matches_worktree", resync),
        patch("roboco.services.workspace._ensure_agent_owned", MagicMock()),
    ):
        await svc.ensure_worktree(root, worktree, "feature/backend/x", "origin/HEAD")

    resync.assert_awaited_once_with(root, worktree)


async def test_failed_install_leaves_marker_for_retry(tmp_path: Path) -> None:
    """A failed staged build writes no marker, so the next ensure retries."""
    root = _clone_root(tmp_path)
    (root / ".venv").mkdir()
    (root / _DEP_INSTALL_MARKER).write_text("old")
    worktree = _worktree(root, "t1", 'version = "1"\n')

    svc = _service()
    failing = AsyncMock(return_value=False)
    with patch.object(WorkspaceService, "_run_dep_install", failing):
        ran = await svc.ensure_venv_matches_worktree(root, worktree)

    assert ran is False
    assert (root / _DEP_INSTALL_MARKER).read_text() == "old"
