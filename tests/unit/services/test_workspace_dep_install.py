"""Unit tests for the post-clone dev-dependency install (issue #10).

Per-agent workspace clones never had the project's dev dependencies
installed, so the `make quality` gate (ruff/mypy/pytest for Python, the TS
toolchain for the panel) was missing and devs re-downloaded tooling per
task. `WorkspaceService.install_dev_deps` now runs `uv sync` / `pnpm install`
after cloning, idempotently (skipped when lockfiles are unchanged).

These tests cover the pure detection/digest helpers and the install method's
ecosystem detection, idempotency, and best-effort failure handling. They run
without a DB or a real git remote.
"""

from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.services.workspace import (
    _DEP_INSTALL_MARKER,
    WorkspaceService,
    _detect_dep_commands,
    _lockfile_digest,
)
from roboco.services.workspace_container import (
    WorkspaceCommand,
    WorkspaceContainerError,
    WorkspaceContainerResult,
)

# Two installs expected when the lockfile changes between calls (named to
# satisfy ruff PLR2004 — magic-value comparison).
_EXPECTED_RERUN_INSTALLS = 2


def _service() -> WorkspaceService:
    """Build a WorkspaceService over a MagicMock session."""
    session = MagicMock()
    session.execute = AsyncMock()
    return WorkspaceService(session)


def _make_workspace(tmp_path: Path) -> Path:
    """A workspace dir with a `.git/` so the marker has somewhere to live."""
    workspace = tmp_path / "roboco" / "backend" / "be-dev-1"
    (workspace / ".git").mkdir(parents=True)
    return workspace


# ---------------------------------------------------------------------------
# _detect_dep_commands
# ---------------------------------------------------------------------------


def test_detect_python_project(tmp_path: Path) -> None:
    """A `pyproject.toml` yields a `uv sync` command."""
    ws = _make_workspace(tmp_path)
    (ws / "pyproject.toml").write_text("[project]\nname = 'x'\n")

    commands = _detect_dep_commands(ws)

    assert commands == [("uv sync --extra dev", ["uv", "sync", "--extra", "dev"])]


def test_detect_pnpm_project(tmp_path: Path) -> None:
    """A `pnpm-lock.yaml` yields a frozen-lockfile pnpm install."""
    ws = _make_workspace(tmp_path)
    (ws / "package.json").write_text("{}")
    (ws / "pnpm-lock.yaml").write_text("lockfileVersion: 9\n")

    commands = _detect_dep_commands(ws)

    assert commands == [("pnpm install", ["pnpm", "install", "--frozen-lockfile"])]


def test_detect_npm_ci_when_package_lock(tmp_path: Path) -> None:
    """`package-lock.json` (no pnpm lock) yields `npm ci`."""
    ws = _make_workspace(tmp_path)
    (ws / "package.json").write_text("{}")
    (ws / "package-lock.json").write_text("{}")

    commands = _detect_dep_commands(ws)

    assert commands == [("npm ci", ["npm", "ci"])]


def test_detect_npm_install_bare_package_json(tmp_path: Path) -> None:
    """A bare `package.json` (no lockfile) falls back to `npm install`."""
    ws = _make_workspace(tmp_path)
    (ws / "package.json").write_text("{}")

    commands = _detect_dep_commands(ws)

    assert commands == [("npm install", ["npm", "install"])]


def test_detect_monorepo_both_ecosystems(tmp_path: Path) -> None:
    """A Python + pnpm monorepo yields both install commands."""
    ws = _make_workspace(tmp_path)
    (ws / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (ws / "package.json").write_text("{}")
    (ws / "pnpm-lock.yaml").write_text("lockfileVersion: 9\n")

    commands = _detect_dep_commands(ws)

    assert ("uv sync --extra dev", ["uv", "sync", "--extra", "dev"]) in commands
    assert ("pnpm install", ["pnpm", "install", "--frozen-lockfile"]) in commands


def test_detect_nothing_to_install(tmp_path: Path) -> None:
    """A repo with no recognized manifest yields no commands."""
    ws = _make_workspace(tmp_path)

    assert _detect_dep_commands(ws) == []


# ---------------------------------------------------------------------------
# _lockfile_digest
# ---------------------------------------------------------------------------


def test_lockfile_digest_none_when_no_lockfiles(tmp_path: Path) -> None:
    """No manifests → None (nothing to hash, nothing to install)."""
    ws = _make_workspace(tmp_path)

    assert _lockfile_digest(ws) is None


def test_lockfile_digest_changes_with_content(tmp_path: Path) -> None:
    """Editing a lockfile changes the digest (so a re-install is triggered)."""
    ws = _make_workspace(tmp_path)
    lock = ws / "uv.lock"
    lock.write_text("a = 1\n")
    digest_a = _lockfile_digest(ws)

    lock.write_text("a = 2\n")
    digest_b = _lockfile_digest(ws)

    assert digest_a is not None
    assert digest_b is not None
    assert digest_a != digest_b


# ---------------------------------------------------------------------------
# install_dev_deps — every install runs in a throwaway workspace container
# ---------------------------------------------------------------------------


def _container(returncode: int = 0, stderr: str = "") -> AsyncMock:
    return AsyncMock(return_value=WorkspaceContainerResult(returncode, "", stderr))


def _python_workspace(tmp_path: Path) -> Path:
    ws = _make_workspace(tmp_path)
    (ws / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (ws / "uv.lock").write_text("version = 1\n")
    return ws


@pytest.mark.asyncio
async def test_install_runs_detected_command(tmp_path: Path) -> None:
    """A Python workspace runs `uv sync` and writes the digest marker."""
    ws = _python_workspace(tmp_path)
    runner = _container()

    with patch("roboco.services.workspace.run_in_workspace_container", runner):
        ran = await _service().install_dev_deps(ws)

    assert ran is True
    assert runner.call_args.args == (
        ws,
        WorkspaceCommand(["uv", "sync", "--extra", "dev"], "dev-deps"),
    )
    assert (ws / _DEP_INSTALL_MARKER).is_file()


@pytest.mark.asyncio
async def test_install_idempotent_on_unchanged_lockfiles(tmp_path: Path) -> None:
    """A second call with the same lockfiles is a no-op (cache hit)."""
    ws = _python_workspace(tmp_path)
    runner = _container()
    svc = _service()

    with patch("roboco.services.workspace.run_in_workspace_container", runner):
        first = await svc.install_dev_deps(ws)
        second = await svc.install_dev_deps(ws)

    assert first is True
    assert second is False
    runner.assert_awaited_once()


@pytest.mark.asyncio
async def test_install_reruns_when_lockfile_changes(tmp_path: Path) -> None:
    """Changing the lockfile invalidates the marker and re-installs."""
    ws = _python_workspace(tmp_path)
    runner = _container()
    svc = _service()

    with patch("roboco.services.workspace.run_in_workspace_container", runner):
        await svc.install_dev_deps(ws)
        (ws / "uv.lock").write_text("version = 2\n")
        await svc.install_dev_deps(ws)

    assert runner.await_count == _EXPECTED_RERUN_INSTALLS


@pytest.mark.asyncio
async def test_install_failure_is_best_effort(tmp_path: Path) -> None:
    """A non-zero install exit logs but does NOT raise, and writes no marker."""
    ws = _python_workspace(tmp_path)

    with patch(
        "roboco.services.workspace.run_in_workspace_container",
        _container(1, "boom"),
    ):
        ran = await _service().install_dev_deps(ws)

    assert ran is False
    # No marker on failure → next call retries.
    assert not (ws / _DEP_INSTALL_MARKER).is_file()


@pytest.mark.asyncio
async def test_install_missing_tool_does_not_raise(tmp_path: Path) -> None:
    """A tool missing from the container image exits 127 in its shell."""
    ws = _python_workspace(tmp_path)

    with patch(
        "roboco.services.workspace.run_in_workspace_container",
        _container(127, "uv: not found"),
    ):
        ran = await _service().install_dev_deps(ws)

    assert ran is False


@pytest.mark.asyncio
async def test_install_container_that_cannot_start_does_not_raise(
    tmp_path: Path,
) -> None:
    ws = _python_workspace(tmp_path)

    with patch(
        "roboco.services.workspace.run_in_workspace_container",
        AsyncMock(side_effect=WorkspaceContainerError("docker unreachable")),
    ):
        ran = await _service().install_dev_deps(ws)

    assert ran is False
    assert not (ws / _DEP_INSTALL_MARKER).is_file()


@pytest.mark.asyncio
async def test_install_skipped_when_disabled(tmp_path: Path) -> None:
    """`workspace_install_dev_deps=False` short-circuits before any container."""
    ws = _python_workspace(tmp_path)
    runner = _container()

    with (
        patch(
            "roboco.services.workspace.settings.workspace_install_dev_deps",
            False,
        ),
        patch("roboco.services.workspace.run_in_workspace_container", runner),
    ):
        ran = await _service().install_dev_deps(ws)

    assert ran is False
    runner.assert_not_called()


@pytest.mark.asyncio
async def test_install_noop_when_no_manifest(tmp_path: Path) -> None:
    """A workspace with no recognized manifest installs nothing."""
    ws = _make_workspace(tmp_path)
    runner = _container()

    with patch("roboco.services.workspace.run_in_workspace_container", runner):
        ran = await _service().install_dev_deps(ws)

    assert ran is False
    runner.assert_not_called()


@pytest.mark.asyncio
async def test_install_never_runs_the_clone_code_in_this_process(
    tmp_path: Path,
) -> None:
    """`uv sync` runs the clone's build backend and `pnpm install` its package
    scripts: as root here that was root on the host. Nothing may run locally,
    and with the agent uid doing the writes no ownership repair follows."""
    ws = _python_workspace(tmp_path)
    (ws / "package.json").write_text("{}")
    (ws / "pnpm-lock.yaml").write_text("lockfileVersion: 9\n")
    runner = _container()

    real_run = subprocess.run

    def _no_local_install(
        argv: list[str], *args: Any, **kw: Any
    ) -> subprocess.CompletedProcess[Any]:
        # Reading lockfiles/markers as the git user is fine; running a
        # package manager here is not.
        if {"uv", "pnpm", "npm"} & set(argv):
            raise AssertionError(f"an install ran in the orchestrator: {argv}")
        return real_run(argv, *args, **kw)

    with (
        patch("roboco.services.workspace.run_in_workspace_container", runner),
        patch("subprocess.run", side_effect=_no_local_install),
    ):
        ran = await _service().install_dev_deps(ws)

    assert ran is True
    assert [call.args[1].argv for call in runner.await_args_list] == [
        ["uv", "sync", "--extra", "dev"],
        ["pnpm", "install", "--frozen-lockfile"],
    ]


# ---------------------------------------------------------------------------
# resync_shared_venv — the shared clone-root venv follows the worktree lockfile
# ---------------------------------------------------------------------------

_INSTALLED_VERSION_FILE = "installed-version"


def _lock_pinning(version: str) -> str:
    return f'[[package]]\nname = "fastapi-guard"\nversion = "{version}"\n'


def _worktree_with_lock(clone_root: Path, version: str) -> Path:
    worktree = clone_root / ".worktrees" / "abcd1234"
    worktree.mkdir(parents=True, exist_ok=True)
    (worktree / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (worktree / "uv.lock").write_text(_lock_pinning(version))
    return worktree


def _legacy_venv(clone_root: Path, version: str) -> None:
    """The clone-time venv: a real `.venv` dir built from the old lockfile."""
    (clone_root / ".venv").mkdir()
    (clone_root / ".venv" / _INSTALLED_VERSION_FILE).write_text(version)


def _fake_uv_sync(
    started: asyncio.Event | None = None, release: asyncio.Event | None = None
) -> AsyncMock:
    """A container runner whose `uv sync` writes the lock's pinned version into
    the venv named by the `env UV_PROJECT_ENVIRONMENT=...` prefix."""

    async def _run(
        cwd: Path, command: WorkspaceCommand, **_kw: Any
    ) -> WorkspaceContainerResult:
        assert command.argv[0] == "env"
        target = Path(command.argv[1].split("=", 1)[1])
        pinned = re.search(r'version = "(.+?)"', (cwd / "uv.lock").read_text())
        assert pinned is not None
        if started is not None and release is not None:
            started.set()
            await release.wait()
        target.mkdir(parents=True)
        (target / _INSTALLED_VERSION_FILE).write_text(pinned.group(1))
        return WorkspaceContainerResult(0, "", "")

    return AsyncMock(side_effect=_run)


def _installed_version(clone_root: Path) -> str:
    return (clone_root / ".venv" / _INSTALLED_VERSION_FILE).read_text()


@pytest.mark.asyncio
async def test_resync_reruns_install_and_changes_marker_when_lockfile_changes(
    tmp_path: Path,
) -> None:
    """Regression: the worktree's lockfile moved, the venv must follow it."""
    clone_root = _make_workspace(tmp_path)
    _legacy_venv(clone_root, "7.3.1")
    worktree = _worktree_with_lock(clone_root, "7.8.2")
    runner = _fake_uv_sync()
    svc = _service()

    with patch("roboco.services.workspace.run_in_workspace_container", runner):
        assert await svc.resync_shared_venv(clone_root, worktree) is True
        first_marker = (clone_root / _DEP_INSTALL_MARKER).read_text()
        assert await svc.resync_shared_venv(clone_root, worktree) is False

        (worktree / "uv.lock").write_text(_lock_pinning("7.9.0"))
        assert await svc.resync_shared_venv(clone_root, worktree) is True

    assert runner.await_count == _EXPECTED_RERUN_INSTALLS
    assert first_marker == _lockfile_digest_of(worktree, "7.8.2")
    assert (clone_root / _DEP_INSTALL_MARKER).read_text() != first_marker


def _lockfile_digest_of(worktree: Path, version: str) -> str | None:
    current = (worktree / "uv.lock").read_text()
    (worktree / "uv.lock").write_text(_lock_pinning(version))
    try:
        return _lockfile_digest(worktree)
    finally:
        (worktree / "uv.lock").write_text(current)


@pytest.mark.asyncio
async def test_resync_installs_the_version_the_lockfile_pins(tmp_path: Path) -> None:
    """After a uv.lock bump the venv holds the newly pinned fastapi-guard."""
    clone_root = _make_workspace(tmp_path)
    _legacy_venv(clone_root, "7.3.1")
    worktree = _worktree_with_lock(clone_root, "7.8.2")

    with patch("roboco.services.workspace.run_in_workspace_container", _fake_uv_sync()):
        await _service().resync_shared_venv(clone_root, worktree)

    assert (clone_root / ".venv").is_symlink()
    assert _installed_version(clone_root) == "7.8.2"


@pytest.mark.asyncio
async def test_resync_leaves_the_old_venv_usable_until_the_swap(
    tmp_path: Path,
) -> None:
    """A sibling worktree on the old venv is not broken while the new one builds,
    and concurrent re-syncs for one lockfile serialize into a single install."""
    clone_root = _make_workspace(tmp_path)
    _legacy_venv(clone_root, "7.3.1")
    worktree = _worktree_with_lock(clone_root, "7.8.2")
    sibling = clone_root / ".worktrees" / "sibling01"
    sibling.mkdir()
    (sibling / ".venv").symlink_to("../../.venv")
    started, release = asyncio.Event(), asyncio.Event()
    runner = _fake_uv_sync(started, release)
    svc = _service()

    with patch("roboco.services.workspace.run_in_workspace_container", runner):
        first = asyncio.create_task(svc.resync_shared_venv(clone_root, worktree))
        await started.wait()
        second = asyncio.create_task(svc.resync_shared_venv(clone_root, worktree))
        await asyncio.sleep(0)

        # Mid-build: the sibling's venv still resolves to the old packages.
        assert (sibling / ".venv" / _INSTALLED_VERSION_FILE).read_text() == "7.3.1"

        release.set()
        assert await first is True
        assert await second is False

    runner.assert_awaited_once()
    assert _installed_version(clone_root) == "7.8.2"
    # The replaced venv survives the swap for anything still running on it.
    legacy = clone_root / ".venv-legacy" / _INSTALLED_VERSION_FILE
    assert legacy.read_text() == "7.3.1"


@pytest.mark.asyncio
async def test_resync_failure_keeps_the_old_venv(tmp_path: Path) -> None:
    """A failed build never touches the venv the worktrees are running on."""
    clone_root = _make_workspace(tmp_path)
    _legacy_venv(clone_root, "7.3.1")
    worktree = _worktree_with_lock(clone_root, "7.8.2")

    with patch(
        "roboco.services.workspace.run_in_workspace_container", _container(1, "boom")
    ):
        assert await _service().resync_shared_venv(clone_root, worktree) is False

    assert not (clone_root / ".venv").is_symlink()
    assert _installed_version(clone_root) == "7.3.1"


@pytest.mark.asyncio
async def test_install_dev_deps_leaves_a_swapped_venv_alone(tmp_path: Path) -> None:
    """The clone-root install must not `uv sync` over a resync-managed venv."""
    ws = _python_workspace(tmp_path)
    (ws / ".venv-abc").mkdir()
    (ws / ".venv").symlink_to(".venv-abc")
    runner = _container()

    with patch("roboco.services.workspace.run_in_workspace_container", runner):
        assert await _service().install_dev_deps(ws) is False

    runner.assert_not_awaited()
