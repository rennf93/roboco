"""Toolchain-matched provisioning: workspace gets the TARGET's Python.

When ``toolchain_match_enabled`` is on and the target declares a Python version,
``install_dev_deps`` provisions the venv against that interpreter (``uv ...
--python <v>``), runs a runnability smoke, and records the result in a workspace
marker the gates read. When off, provisioning is exactly today's behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.services.workspace import (
    WorkspaceService,
    _detect_dep_commands,
)
from roboco.services.workspace_container import WorkspaceContainerResult

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

_PY = '[project]\nname = "x"\nrequires-python = ">=3.14,<3.15"\n'


def _service() -> WorkspaceService:
    session = MagicMock()
    session.execute = AsyncMock()
    return WorkspaceService(session)


def _make_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "roboco" / "backend" / "be-dev-1"
    (workspace / ".git").mkdir(parents=True)
    return workspace


def test_detect_dep_commands_injects_target_python(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    (ws / "pyproject.toml").write_text(_PY)
    commands = _detect_dep_commands(ws, target_python="3.14")
    assert commands[0][1] == ["uv", "sync", "--extra", "dev", "--python", "3.14"]


def test_detect_dep_commands_no_python_is_unchanged(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    (ws / "pyproject.toml").write_text(_PY)
    commands = _detect_dep_commands(ws)
    assert commands[0][1] == ["uv", "sync", "--extra", "dev"]


def _fake_container_factory(
    captured: list[list[str]], *, collect_rc: int
) -> Callable[..., Awaitable[WorkspaceContainerResult]]:
    """Stands in for the throwaway workspace container the install + smoke use."""

    async def _fake_container(
        _cwd: Path, argv: list[str], **_kw: object
    ) -> WorkspaceContainerResult:
        captured.append(list(argv))
        rc = collect_rc if "--collect-only" in argv else 0
        return WorkspaceContainerResult(rc, "", "")

    return _fake_container


@pytest.mark.asyncio
async def test_provisions_target_python_and_records_ok(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    (ws / "pyproject.toml").write_text(_PY)
    (ws / "uv.lock").write_text("version = 1\n")
    svc = _service()
    captured: list[list[str]] = []

    with (
        patch("roboco.services.workspace.settings.toolchain_match_enabled", True),
        patch(
            "roboco.services.workspace.run_in_workspace_container",
            new=_fake_container_factory(captured, collect_rc=0),
        ),
    ):
        await svc.install_dev_deps(ws)

    # provisioned against 3.14 + ran the collect smoke
    assert ["uv", "sync", "--extra", "dev", "--python", "3.14"] in captured
    assert any("--collect-only" in argv for argv in captured)
    assert svc.read_toolchain_status(ws) == ("3.14", "ok")


@pytest.mark.asyncio
async def test_records_broken_on_collection_error(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    (ws / "pyproject.toml").write_text(_PY)
    (ws / "uv.lock").write_text("version = 1\n")
    svc = _service()
    captured: list[list[str]] = []

    with (
        patch("roboco.services.workspace.settings.toolchain_match_enabled", True),
        patch(
            "roboco.services.workspace.run_in_workspace_container",
            new=_fake_container_factory(captured, collect_rc=2),  # pytest collect error
        ),
    ):
        await svc.install_dev_deps(ws)

    assert svc.read_toolchain_status(ws) == ("3.14", "broken")


@pytest.mark.asyncio
async def test_flag_off_is_unchanged_no_marker(tmp_path: Path) -> None:
    ws = _make_workspace(tmp_path)
    (ws / "pyproject.toml").write_text(_PY)
    (ws / "uv.lock").write_text("version = 1\n")
    svc = _service()
    captured: list[list[str]] = []

    with (
        patch("roboco.services.workspace.settings.toolchain_match_enabled", False),
        patch(
            "roboco.services.workspace.run_in_workspace_container",
            new=_fake_container_factory(captured, collect_rc=0),
        ),
    ):
        await svc.install_dev_deps(ws)

    assert ["uv", "sync", "--extra", "dev"] in captured
    assert not any("--python" in argv for argv in captured)
    assert not any("--collect-only" in argv for argv in captured)
    assert svc.read_toolchain_status(ws) == (None, None)
