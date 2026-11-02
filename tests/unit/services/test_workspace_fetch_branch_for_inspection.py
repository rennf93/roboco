"""fetch_branch_for_inspection's subprocess_timeout override (adversarial-review
round-2 fix 3): the fetch subprocess must self-bound near the caller's own leg
budget instead of running up to workspace_clone_timeout (300s) on the shared
DEFAULT asyncio executor (asyncio.to_thread, not git.py's dedicated
_GIT_EXECUTOR) after the caller has already given up waiting on it. A
timeout there now raises GitTimeoutError (mirroring _run_git's own
TimeoutExpired -> GitTimeoutError conversion in git.py), not a raw
subprocess.TimeoutExpired, so a bounded caller (run_bounded_leg) catches it
the same way as every other git-touching leg.

The clone-CREATION step (ensure_workspace, stubbed out here) is untouched by
this fix and keeps its own workspace_clone_timeout (300s) unconditionally —
these tests isolate the FETCH only: the relay's fetch from origin and the
workspace's fetch from the relay both carry the bound.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.exceptions import GitTimeoutError
from roboco.services.workspace import WorkspaceError, WorkspaceService

# Named constant to satisfy ruff PLR2004 (magic value in comparison).
_EXPECTED_TIMEOUT_SECONDS = 5


def _service() -> WorkspaceService:
    session = MagicMock()
    session.execute = AsyncMock()
    return WorkspaceService(session)


def _bind(svc: WorkspaceService, name: str, value: object) -> None:
    """Stub `name` on `svc` without tripping mypy's method-assign check."""
    object.__setattr__(svc, name, value)


def _wire_resolution(svc: WorkspaceService, workspace: Path) -> None:
    """Stub the resolution steps ahead of the fetch subprocess so only the
    fetch itself is under test."""
    _bind(svc, "_resolve_branch_to_project_slug", AsyncMock(return_value="roboco"))
    _bind(svc, "ensure_workspace", AsyncMock(return_value=workspace))


def _relay(fetch: Any) -> MagicMock:
    relay = MagicMock(path=Path("/data/private/relays/roboco.git"))
    relay.fetch_from_origin.side_effect = fetch
    return relay


def _done(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")


async def _fetch_capturing_timeouts(
    tmp_path: Path, subprocess_timeout: float | None
) -> list[object]:
    svc = _service()
    _wire_resolution(svc, tmp_path)
    captured: list[object] = []

    def _capture(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.append(kwargs.get("timeout"))
        return _done()

    _bind(svc, "_relay_for", AsyncMock(return_value=_relay(_capture)))
    with patch("roboco.services.workspace.run_workspace_git", side_effect=_capture):
        if subprocess_timeout is None:
            await svc.fetch_branch_for_inspection(
                agent_id=uuid4(), branch_name="feature/x"
            )
        else:
            await svc.fetch_branch_for_inspection(
                agent_id=uuid4(),
                branch_name="feature/x",
                subprocess_timeout=subprocess_timeout,
            )
    return captured


@pytest.mark.asyncio
async def test_default_subprocess_timeout_is_workspace_clone_timeout(
    tmp_path: Path,
) -> None:
    """Every EXISTING caller omits subprocess_timeout — the relay fetch and
    the workspace fetch keep the 300s workspace_clone_timeout."""
    captured = await _fetch_capturing_timeouts(tmp_path, None)
    assert captured == [settings.workspace_clone_timeout] * 2


@pytest.mark.asyncio
async def test_subprocess_timeout_override_reaches_the_fetch(tmp_path: Path) -> None:
    captured = await _fetch_capturing_timeouts(tmp_path, 12.5)
    assert captured == [12.5, 12.5]


@pytest.mark.asyncio
async def test_timeout_expired_becomes_git_timeout_error(tmp_path: Path) -> None:
    """Mirrors _run_git's own TimeoutExpired -> GitTimeoutError conversion
    (git.py) so run_bounded_leg catches this uniformly with every other
    git-touching leg, instead of a raw subprocess.TimeoutExpired propagating
    uncaught to the RobocoError handler."""
    svc = _service()
    _wire_resolution(svc, tmp_path)

    def _hang(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        timeout = kwargs.get("timeout")
        raise subprocess.TimeoutExpired(
            cmd="git fetch",
            timeout=float(timeout) if isinstance(timeout, int | float) else 0,
        )

    _bind(svc, "_relay_for", AsyncMock(return_value=_relay(_hang)))
    with pytest.raises(GitTimeoutError) as exc_info:
        await svc.fetch_branch_for_inspection(
            agent_id=uuid4(), branch_name="feature/x", subprocess_timeout=5.0
        )
    assert exc_info.value.timeout == _EXPECTED_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_an_unknown_project_raises_before_any_fetch(tmp_path: Path) -> None:
    svc = _service()
    _wire_resolution(svc, tmp_path)
    _bind(svc, "_relay_for", AsyncMock(return_value=None))
    with pytest.raises(WorkspaceError, match="Project not found"):
        await svc.fetch_branch_for_inspection(agent_id=uuid4(), branch_name="feature/x")
