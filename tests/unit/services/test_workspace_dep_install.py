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

import subprocess
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.services.workspace import (
    _DEP_INSTALL_MARKER,
    WorkspaceService,
    _detect_dep_commands,
    _lockfile_digest,
    _own_install_outputs,
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
# install_dev_deps
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_runs_detected_command(tmp_path: Path) -> None:
    """A Python workspace runs `uv sync` and writes the digest marker."""
    ws = _make_workspace(tmp_path)
    (ws / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (ws / "uv.lock").write_text("version = 1\n")

    svc = _service()
    captured: list[list[str]] = []

    def _fake_run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        captured.append(argv)
        return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

    with (
        patch("roboco.services.workspace.subprocess.run", side_effect=_fake_run),
        patch("roboco.services.workspace._ensure_agent_owned"),
    ):
        ran = await svc.install_dev_deps(ws)

    assert ran is True
    assert ["uv", "sync", "--extra", "dev"] in captured
    assert (ws / _DEP_INSTALL_MARKER).is_file()


@pytest.mark.asyncio
async def test_install_idempotent_on_unchanged_lockfiles(tmp_path: Path) -> None:
    """A second call with the same lockfiles is a no-op (cache hit)."""
    ws = _make_workspace(tmp_path)
    (ws / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (ws / "uv.lock").write_text("version = 1\n")

    svc = _service()
    run_count = 0

    def _fake_run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        nonlocal run_count
        run_count += 1
        return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

    with (
        patch("roboco.services.workspace.subprocess.run", side_effect=_fake_run),
        patch("roboco.services.workspace._ensure_agent_owned"),
    ):
        first = await svc.install_dev_deps(ws)
        second = await svc.install_dev_deps(ws)

    assert first is True
    assert second is False
    assert run_count == 1


@pytest.mark.asyncio
async def test_install_reruns_when_lockfile_changes(tmp_path: Path) -> None:
    """Changing the lockfile invalidates the marker and re-installs."""
    ws = _make_workspace(tmp_path)
    (ws / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    lock = ws / "uv.lock"
    lock.write_text("version = 1\n")

    svc = _service()
    run_count = 0

    def _fake_run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        nonlocal run_count
        run_count += 1
        return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

    with (
        patch("roboco.services.workspace.subprocess.run", side_effect=_fake_run),
        patch("roboco.services.workspace._ensure_agent_owned"),
    ):
        await svc.install_dev_deps(ws)
        lock.write_text("version = 2\n")
        await svc.install_dev_deps(ws)

    assert run_count == _EXPECTED_RERUN_INSTALLS


@pytest.mark.asyncio
async def test_install_failure_is_best_effort(tmp_path: Path) -> None:
    """A non-zero install exit logs but does NOT raise, and writes no marker."""
    ws = _make_workspace(tmp_path)
    (ws / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (ws / "uv.lock").write_text("version = 1\n")

    svc = _service()

    def _fake_run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, returncode=1, stdout="", stderr="boom")

    with (
        patch("roboco.services.workspace.subprocess.run", side_effect=_fake_run),
        patch("roboco.services.workspace._ensure_agent_owned"),
    ):
        ran = await svc.install_dev_deps(ws)

    assert ran is False
    # No marker on failure → next call retries.
    assert not (ws / _DEP_INSTALL_MARKER).is_file()


@pytest.mark.asyncio
async def test_install_missing_tool_does_not_raise(tmp_path: Path) -> None:
    """A missing `uv`/`pnpm` on the host is swallowed (FileNotFoundError)."""
    ws = _make_workspace(tmp_path)
    (ws / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (ws / "uv.lock").write_text("version = 1\n")

    svc = _service()

    def _fake_run(*_a: object, **_kw: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("uv not found")

    with (
        patch("roboco.services.workspace.subprocess.run", side_effect=_fake_run),
        patch("roboco.services.workspace._ensure_agent_owned"),
    ):
        ran = await svc.install_dev_deps(ws)

    assert ran is False


@pytest.mark.asyncio
async def test_install_skipped_when_disabled(tmp_path: Path) -> None:
    """`workspace_install_dev_deps=False` short-circuits before any subprocess."""
    ws = _make_workspace(tmp_path)
    (ws / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (ws / "uv.lock").write_text("version = 1\n")

    svc = _service()

    with (
        patch(
            "roboco.services.workspace.settings.workspace_install_dev_deps",
            False,
        ),
        patch("roboco.services.workspace.subprocess.run") as run_mock,
        patch("roboco.services.workspace._ensure_agent_owned"),
    ):
        ran = await svc.install_dev_deps(ws)

    assert ran is False
    run_mock.assert_not_called()


@pytest.mark.asyncio
async def test_install_noop_when_no_manifest(tmp_path: Path) -> None:
    """A workspace with no recognized manifest installs nothing."""
    ws = _make_workspace(tmp_path)
    svc = _service()

    with (
        patch("roboco.services.workspace.subprocess.run") as run_mock,
        patch("roboco.services.workspace._ensure_agent_owned"),
    ):
        ran = await svc.install_dev_deps(ws)

    assert ran is False
    run_mock.assert_not_called()


@pytest.mark.asyncio
async def test_install_invalidates_owned_marker_before_running(tmp_path: Path) -> None:
    """The root-run install drops the ownership sentinel first, so the repair
    walk after it cannot be skipped by a stale marker and leave root-owned
    .venv files behind (live 2026-09: 1212 root-owned files in agent venvs)."""
    ws = _make_workspace(tmp_path)
    (ws / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (ws / "uv.lock").write_text("version = 1\n")

    svc = _service()
    order: list[str] = []

    def _fake_run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        order.append("install")
        return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

    with (
        patch("roboco.services.workspace.subprocess.run", side_effect=_fake_run),
        patch(
            "roboco.services.workspace.invalidate_owned_marker",
            side_effect=lambda _ws: order.append("invalidate"),
        ),
        patch(
            "roboco.services.workspace._own_install_outputs",
            side_effect=lambda _ws: order.append("own_outputs"),
        ),
        patch(
            "roboco.services.workspace._ensure_agent_owned",
            side_effect=lambda _ws: order.append("repair"),
        ),
    ):
        ran = await svc.install_dev_deps(ws)

    assert ran is True
    assert order == ["invalidate", "install", "own_outputs", "repair"]


# ---------------------------------------------------------------------------
# _own_install_outputs
# ---------------------------------------------------------------------------


def test_own_install_outputs_walks_venv_and_node_modules(tmp_path: Path) -> None:
    """.venv and node_modules are walked in full (never pruned here) so every
    dir and file under them gets chowned; files outside them are untouched."""
    ws = _make_workspace(tmp_path)
    venv_bin = ws / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python").write_text("#!/bin/sh\n")
    site_pkg = ws / ".venv" / "lib" / "site-packages" / "pkg"
    site_pkg.mkdir(parents=True)
    (site_pkg / "__init__.py").write_text("")
    node_pkg = ws / "node_modules" / "foo"
    node_pkg.mkdir(parents=True)
    (node_pkg / "index.js").write_text("module.exports = {}\n")
    (ws / "src").mkdir()
    (ws / "src" / "main.py").write_text("x = 1\n")

    touched: list[str] = []

    def _record(entry: str) -> int:
        touched.append(entry)
        return 0

    with patch("roboco.services.workspace._own_and_grant_rw", side_effect=_record):
        _own_install_outputs(ws)

    assert len(touched) == len(set(touched))
    expected = {
        str(ws / ".venv"),
        str(ws / ".venv" / "bin"),
        str(ws / ".venv" / "bin" / "python"),
        str(ws / ".venv" / "lib"),
        str(ws / ".venv" / "lib" / "site-packages"),
        str(ws / ".venv" / "lib" / "site-packages" / "pkg"),
        str(ws / ".venv" / "lib" / "site-packages" / "pkg" / "__init__.py"),
        str(ws / "node_modules"),
        str(ws / "node_modules" / "foo"),
        str(ws / "node_modules" / "foo" / "index.js"),
    }
    assert set(touched) == expected
    assert str(ws / "src" / "main.py") not in touched


def test_own_install_outputs_does_not_follow_a_symlinked_venv(tmp_path: Path) -> None:
    """A worktree's .venv is a symlink to the clone root's shared .venv
    (_link_shared_venv); the link itself gets owned, its target never walked."""
    target = tmp_path / "shared-venv"
    (target / "bin").mkdir(parents=True)
    (target / "bin" / "python").write_text("#!/bin/sh\n")
    ws = _make_workspace(tmp_path)
    (ws / ".venv").symlink_to(target)

    touched: list[str] = []

    def _record(entry: str) -> int:
        touched.append(entry)
        return 0

    with patch("roboco.services.workspace._own_and_grant_rw", side_effect=_record):
        _own_install_outputs(ws)

    assert touched.count(str(ws / ".venv")) <= 1
    assert not any(str(target) in entry for entry in touched)


@pytest.mark.asyncio
async def test_install_owns_venv_after_root_install(tmp_path: Path) -> None:
    """The root-run uv sync writes .venv; _own_install_outputs must chown it
    since the main walk (_ensure_agent_owned) prunes .venv as agent-created."""
    ws = _make_workspace(tmp_path)
    (ws / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (ws / "uv.lock").write_text("version = 1\n")

    svc = _service()
    owned: list[str] = []

    def _record_own(entry: str) -> int:
        owned.append(entry)
        return 0

    def _fake_run(argv: list[str], **_kw: object) -> subprocess.CompletedProcess[str]:
        venv_bin = ws / ".venv" / "bin"
        venv_bin.mkdir(parents=True, exist_ok=True)
        (venv_bin / "python").write_text("#!/bin/sh\n")
        return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

    with (
        patch("roboco.services.workspace.subprocess.run", side_effect=_fake_run),
        patch("roboco.services.workspace._own_and_grant_rw", side_effect=_record_own),
    ):
        ran = await svc.install_dev_deps(ws)

    assert ran is True
    assert str(ws / ".venv" / "bin" / "python") in owned
