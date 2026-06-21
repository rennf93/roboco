"""End-to-end: resolver -> provisioning -> marker -> read -> gate guard.

Ties the toolchain-matching pieces together at the logic level (no Docker, no
network): a target whose .python-version conflicts with requires-python is
provisioned against the requires-python interpreter; a collection error records
'broken'; the gate guard then refuses a delivery pass. Flag-off is inert.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from roboco.services.workspace import WorkspaceService

if TYPE_CHECKING:
    from pathlib import Path

# requires-python needs 3.14 but the pin says 3.13 — the resolver must ignore
# the conflicting pin and provision against 3.14.
_CONFLICTING_PYPROJECT = '[project]\nname = "x"\nrequires-python = ">=3.14,<3.15"\n'


def _service() -> WorkspaceService:
    session = MagicMock()
    session.execute = AsyncMock()
    return WorkspaceService(session)


def _make_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "roboco" / "backend" / "be-dev-1"
    (ws / ".git").mkdir(parents=True)
    (ws / "pyproject.toml").write_text(_CONFLICTING_PYPROJECT)
    (ws / ".python-version").write_text("3.13\n")
    (ws / "uv.lock").write_text("version = 1\n")
    return ws


def _choreographer_for(status: str | None) -> Choreographer:
    base: dict[str, Any] = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    base["git"].toolchain_status_for_task.return_value = status
    return Choreographer(ChoreographerDeps(**base))


def _fake_run(captured: list[list[str]], *, collect_rc: int):
    def _run(argv, **_kw) -> subprocess.CompletedProcess[str]:
        captured.append(argv)
        rc = collect_rc if "--collect-only" in argv else 0
        return subprocess.CompletedProcess(argv, returncode=rc, stdout="", stderr="")

    return _run


@pytest.mark.asyncio
async def test_broken_env_provisions_then_blocks_the_gate(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "toolchain_match_enabled", True)
    ws = _make_workspace(tmp_path)
    svc = _service()
    captured: list[list[str]] = []

    with (
        patch("roboco.services.workspace.settings.toolchain_match_enabled", True),
        patch(
            "roboco.services.workspace.subprocess.run",
            side_effect=_fake_run(captured, collect_rc=2),  # collection/import error
        ),
        patch("roboco.services.workspace._ensure_agent_owned"),
    ):
        await svc.install_dev_deps(ws)

    # 1. Resolver ignored the conflicting .python-version (3.13) and provisioned 3.14.
    assert ["uv", "sync", "--extra", "dev", "--python", "3.14"] in captured
    # 2. The collection error was recorded as 'broken'.
    assert svc.read_toolchain_status(ws) == ("3.14", "broken")

    # 3. A gate sees 'broken' (via the git service read) and refuses the pass.
    _python, status = svc.read_toolchain_status(ws)
    chor = _choreographer_for(status)
    env = await chor._toolchain_broken_guard(uuid4(), MagicMock())
    assert env is not None and env.as_dict()["error"] == "invalid_state"


@pytest.mark.asyncio
async def test_ok_env_provisions_and_gate_proceeds(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "toolchain_match_enabled", True)
    ws = _make_workspace(tmp_path)
    svc = _service()
    captured: list[list[str]] = []

    with (
        patch("roboco.services.workspace.settings.toolchain_match_enabled", True),
        patch(
            "roboco.services.workspace.subprocess.run",
            side_effect=_fake_run(captured, collect_rc=0),
        ),
        patch("roboco.services.workspace._ensure_agent_owned"),
    ):
        await svc.install_dev_deps(ws)

    assert svc.read_toolchain_status(ws) == ("3.14", "ok")
    chor = _choreographer_for("ok")
    assert await chor._toolchain_broken_guard(uuid4(), MagicMock()) is None


@pytest.mark.asyncio
async def test_flag_off_provisions_today_and_never_blocks(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(settings, "toolchain_match_enabled", False)
    ws = _make_workspace(tmp_path)
    svc = _service()
    captured: list[list[str]] = []

    with (
        patch("roboco.services.workspace.settings.toolchain_match_enabled", False),
        patch(
            "roboco.services.workspace.subprocess.run",
            side_effect=_fake_run(captured, collect_rc=2),
        ),
        patch("roboco.services.workspace._ensure_agent_owned"),
    ):
        await svc.install_dev_deps(ws)

    assert ["uv", "sync", "--extra", "dev"] in captured
    assert not any("--python" in argv for argv in captured)
    assert svc.read_toolchain_status(ws) == (None, None)
    chor = _choreographer_for(None)
    assert await chor._toolchain_broken_guard(uuid4(), MagicMock()) is None
