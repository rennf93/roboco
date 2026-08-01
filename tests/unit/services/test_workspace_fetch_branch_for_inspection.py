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
these tests isolate the FETCH subprocess only.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.config import settings
from roboco.exceptions import GitTimeoutError
from roboco.services.workspace import WorkspaceService

if TYPE_CHECKING:
    from pathlib import Path

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


def _no_project_service_patch() -> Any:
    """No git token (project=None skips the decrypt path entirely)."""
    return patch(
        "roboco.services.project.get_project_service",
        return_value=MagicMock(get_by_slug=AsyncMock(return_value=None)),
    )


@pytest.mark.asyncio
async def test_default_subprocess_timeout_is_workspace_clone_timeout(
    tmp_path: Path,
) -> None:
    """Every EXISTING caller omits subprocess_timeout — behavior byte-for-byte
    unchanged: the fetch subprocess keeps the 300s workspace_clone_timeout."""
    svc = _service()
    _wire_resolution(svc, tmp_path)
    captured: list[object] = []

    def _fake_run(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.append(kwargs.get("timeout"))
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with (
        patch("roboco.services.workspace.subprocess.run", side_effect=_fake_run),
        patch("roboco.services.workspace._ensure_agent_owned"),
        _no_project_service_patch(),
    ):
        await svc.fetch_branch_for_inspection(agent_id=uuid4(), branch_name="feature/x")

    assert captured == [settings.workspace_clone_timeout]


@pytest.mark.asyncio
async def test_subprocess_timeout_override_reaches_the_fetch(tmp_path: Path) -> None:
    svc = _service()
    _wire_resolution(svc, tmp_path)
    captured: list[object] = []

    def _fake_run(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.append(kwargs.get("timeout"))
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    with (
        patch("roboco.services.workspace.subprocess.run", side_effect=_fake_run),
        patch("roboco.services.workspace._ensure_agent_owned"),
        _no_project_service_patch(),
    ):
        await svc.fetch_branch_for_inspection(
            agent_id=uuid4(), branch_name="feature/x", subprocess_timeout=12.5
        )

    assert captured == [12.5]


@pytest.mark.asyncio
async def test_timeout_expired_becomes_git_timeout_error(tmp_path: Path) -> None:
    """Mirrors _run_git's own TimeoutExpired -> GitTimeoutError conversion
    (git.py) so run_bounded_leg catches this uniformly with every other
    git-touching leg, instead of a raw subprocess.TimeoutExpired propagating
    uncaught to the RobocoError handler."""
    svc = _service()
    _wire_resolution(svc, tmp_path)

    def _fake_run(*_args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        timeout = kwargs.get("timeout")
        raise subprocess.TimeoutExpired(
            cmd="git fetch",
            timeout=float(timeout) if isinstance(timeout, int | float) else 0,
        )

    with (
        patch("roboco.services.workspace.subprocess.run", side_effect=_fake_run),
        patch("roboco.services.workspace._ensure_agent_owned"),
        _no_project_service_patch(),
        pytest.raises(GitTimeoutError) as exc_info,
    ):
        await svc.fetch_branch_for_inspection(
            agent_id=uuid4(), branch_name="feature/x", subprocess_timeout=5.0
        )
    assert exc_info.value.timeout == _EXPECTED_TIMEOUT_SECONDS
