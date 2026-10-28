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

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.services.workspace import (
    _DEP_INSTALL_MARKER,
    WorkspaceService,
    _detect_dep_commands,
    _lockfile_digest,
)
from roboco.services.workspace_container import (
    WorkspaceContainerError,
    WorkspaceContainerResult,
)

if TYPE_CHECKING:
    from pathlib import Path

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
    assert runner.call_args.args == (ws, ["uv", "sync", "--extra", "dev"])
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

    def _forbidden(*_a: object, **_kw: object) -> None:
        raise AssertionError("an install ran in the orchestrator process")

    with (
        patch("roboco.services.workspace.run_in_workspace_container", runner),
        patch("roboco.services.workspace.subprocess.run", side_effect=_forbidden),
        patch("roboco.services.workspace._ensure_agent_owned", side_effect=_forbidden),
    ):
        ran = await _service().install_dev_deps(ws)

    assert ran is True
    assert [call.args[1] for call in runner.await_args_list] == [
        ["uv", "sync", "--extra", "dev"],
        ["pnpm", "install", "--frozen-lockfile"],
    ]
