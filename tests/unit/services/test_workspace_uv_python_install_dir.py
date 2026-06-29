"""Per-workspace ``UV_PYTHON_INSTALL_DIR`` — the workspace-venv brick cure (Fix 2).

Root cause (live on be-dev-1, project requires Python 3.14): ``install_dev_deps``
runs ``uv sync --python 3.14`` as ROOT in the orchestrator, so uv fetches the
managed CPython into its default ``/root/.local/share/uv/python`` (root-owned,
``/root`` is 0700). The workspace ``.venv/bin/python`` symlinks there, the
symlink target is OUTSIDE the workspace bind mount, and ``_ensure_agent_owned``
can't chown it — so the agent (uid 1000) hits ``Permission denied (os error 13)``
canonicalizing ``.venv/bin/python3`` and every ``uv run`` dies. Fix 1 (bash-guard)
protects the sacred ``/app/.venv`` but does NOT cure this.

Cure: pin ``UV_PYTHON_INSTALL_DIR`` to ``<workspace>/.uv-python`` so the managed
CPython the venv symlinks to lives INSIDE the workspace bind mount and is chowned
to the agent by the existing ``_ensure_agent_owned`` walk (``.uv-python`` is not
in ``_PRUNE_DIRS``). Per-workspace → per-project isolation intact (no global
shared interpreter). ``/app/.venv`` untouched.
"""

from __future__ import annotations

import subprocess
from pathlib import Path as _Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.services.workspace import (
    _PRUNE_DIRS,
    WorkspaceService,
    _uv_subprocess_env,
)

if TYPE_CHECKING:
    from pathlib import Path


def _service() -> WorkspaceService:
    session = MagicMock()
    session.execute = AsyncMock()
    return WorkspaceService(session)


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "roboco" / "backend" / "be-dev-1"
    (workspace / ".git").mkdir(parents=True)
    return workspace


# ---------------------------------------------------------------------------
# _PRUNE_DIRS — the chown walk must reach .uv-python
# ---------------------------------------------------------------------------


def test_uv_python_install_dir_not_pruned() -> None:
    # If .uv-python were pruned, _ensure_agent_owned would never chown the
    # managed CPython and the agent still couldn't traverse it.
    assert ".uv-python" not in _PRUNE_DIRS


def test_repo_gitignore_ignores_uv_python_dir() -> None:
    # The per-workspace managed-CPython dir lives inside the clone (and thus
    # inside every worktree checkout). It must be gitignored so an agent never
    # commits a multi-GB CPython fetch.
    gitignore = _Path(__file__).resolve().parents[3] / ".gitignore"
    assert gitignore.exists(), f".gitignore not found at {gitignore}"
    lines = gitignore.read_text().splitlines()
    assert ".uv-python/" in lines, ".uv-python/ must be gitignored (now per-workspace)"


def test_uv_subprocess_env_clone_root_when_cwd_is_worktree(tmp_path: Path) -> None:
    # F123: a task's worktree is a separate checkout, but .venv / .uv-python stay
    # at the CLONE root (shared). A uv run launched from a worktree CWD must still
    # pin UV_PYTHON_INSTALL_DIR at the clone root's .uv-python — not a phantom
    # <worktree>/.uv-python — so the managed CPython is found and not re-fetched
    # per worktree.
    clone = tmp_path / "roboco" / "backend" / "be-dev-1"
    worktree = clone / ".worktrees" / "a3c40fe7"
    worktree.mkdir(parents=True)

    env = _uv_subprocess_env(worktree)

    assert env["UV_PYTHON_INSTALL_DIR"] == str(clone / ".uv-python")


def test_uv_subprocess_env_clone_root_unchanged_for_clone_itself(
    tmp_path: Path,
) -> None:
    # Regression guard: when the CWD IS the clone root (no .worktrees segment),
    # behavior is byte-for-byte the pre-worktree path.
    clone = tmp_path / "roboco" / "backend" / "be-dev-1"
    clone.mkdir(parents=True)

    env = _uv_subprocess_env(clone)

    assert env["UV_PYTHON_INSTALL_DIR"] == str(clone / ".uv-python")


# ---------------------------------------------------------------------------
# _run_dep_install — uv must fetch the managed CPython into the workspace
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_dep_install_sets_uv_python_install_dir(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    svc = _service()
    captured_env: dict[str, str] = {}

    def _fake_run(
        argv: list[str], *, env: dict[str, str] | None = None, **_kw: object
    ) -> subprocess.CompletedProcess[str]:
        if env is not None:
            captured_env.update(env)
        return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

    with patch("roboco.services.workspace.subprocess.run", side_effect=_fake_run):
        await svc._run_dep_install(
            ws, "uv sync --extra dev", ["uv", "sync", "--extra", "dev"]
        )

    assert "UV_PYTHON_INSTALL_DIR" in captured_env
    # Must point INSIDE the workspace bind mount (the brick was the managed
    # CPython landing in /root, outside the mount + root-owned).
    assert captured_env["UV_PYTHON_INSTALL_DIR"] == str(ws / ".uv-python")


@pytest.mark.asyncio
async def test_toolchain_smoke_sets_uv_python_install_dir(tmp_path: Path) -> None:
    # The smoke also runs `uv run --python <ver>` and would otherwise fetch the
    # managed CPython into /root a second time.
    ws = _make_workspace(tmp_path)
    svc = _service()
    captured_env: dict[str, str] = {}

    def _fake_run(
        argv: list[str], *, env: dict[str, str] | None = None, **_kw: object
    ) -> subprocess.CompletedProcess[str]:
        if env is not None:
            captured_env.update(env)
        return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

    with patch("roboco.services.workspace.subprocess.run", side_effect=_fake_run):
        await svc._run_toolchain_smoke(ws, "3.14")

    assert captured_env.get("UV_PYTHON_INSTALL_DIR") == str(ws / ".uv-python")


@pytest.mark.asyncio
async def test_install_dev_deps_uv_python_dir_inside_workspace(tmp_path: Path) -> None:
    # End-to-end: install_dev_deps runs uv with UV_PYTHON_INSTALL_DIR pointing
    # inside the workspace, so the managed CPython is on the shared volume and
    # gets chowned to the agent. Regression guard for the live be-dev-1 brick.
    ws = _make_workspace(tmp_path)
    (ws / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (ws / "uv.lock").write_text("version = 1\n")
    svc = _service()
    captured_env: dict[str, str] = {}

    def _fake_run(
        argv: list[str], *, env: dict[str, str] | None = None, **_kw: object
    ) -> subprocess.CompletedProcess[str]:
        if env is not None:
            captured_env.update(env)
        return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

    with (
        patch("roboco.services.workspace.subprocess.run", side_effect=_fake_run),
        patch("roboco.services.workspace._ensure_agent_owned"),
    ):
        await svc.install_dev_deps(ws)

    assert captured_env.get("UV_PYTHON_INSTALL_DIR") == str(ws / ".uv-python")


@pytest.mark.asyncio
async def test_install_env_inherits_parent_environ(tmp_path: Path) -> None:
    # The injected env must still carry PATH etc. (uv must be found) — we merge
    # into os.environ, not replace it.
    ws = _make_workspace(tmp_path)
    svc = _service()
    captured_env: dict[str, str] = {}

    def _fake_run(
        argv: list[str], *, env: dict[str, str] | None = None, **_kw: object
    ) -> subprocess.CompletedProcess[str]:
        if env is not None:
            captured_env.update(env)
        return subprocess.CompletedProcess(argv, returncode=0, stdout="", stderr="")

    with (
        patch("roboco.services.workspace.subprocess.run", side_effect=_fake_run),
        patch.dict(
            "os.environ",
            {"PATH": "/usr/bin:/bin", "ROBOCO_TEST_MARKER": "1"},
            clear=False,
        ),
    ):
        await svc._run_dep_install(ws, "uv sync", ["uv", "sync"])

    assert captured_env.get("PATH") == "/usr/bin:/bin"
    assert captured_env.get("ROBOCO_TEST_MARKER") == "1"
    assert captured_env.get("UV_PYTHON_INSTALL_DIR") == str(ws / ".uv-python")
