"""Unit tests for `WorkspaceService.ensure_workspace` refresh behavior.

Audit H26: when a PM/Doc is respawned and re-enters `ensure_workspace`
on an already-healthy clone, the previous implementation short-circuited
with no fetch — leaving the agent looking at arbitrarily stale refs.
These tests pin the new behavior: every healthy short-circuit MUST run
`git fetch origin` (best-effort).
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.services.workspace import WorkspaceService

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

# Minimum tokens in a valid `git fetch origin [...]` argv (`git`, `fetch`,
# `origin`). Named to satisfy ruff PLR2004 — magic-value comparison.
_MIN_GIT_FETCH_ARGC = 3


def _service() -> WorkspaceService:
    """Build a WorkspaceService over a MagicMock session."""
    session = MagicMock()
    session.execute = AsyncMock()
    return WorkspaceService(session)


def _bind(svc: WorkspaceService, name: str, value: object) -> None:
    """Stub `name` on `svc` without tripping mypy's method-assign check."""
    object.__setattr__(svc, name, value)


def _fake_agent(slug: str = "be-pm") -> MagicMock:
    """Build a MagicMock that satisfies the AgentTable surface used here."""
    agent = MagicMock()
    agent.id = uuid4()
    agent.slug = slug
    # WorkspaceService reads .team and falls back to BACKEND if falsy.
    agent.team = None
    return agent


@pytest.fixture
def healthy_workspace(tmp_path: Path) -> Iterator[Path]:
    """Materialize a directory that passes `_is_workspace_healthy`."""
    workspace = tmp_path / "roboco" / "backend" / "be-pm"
    git_dir = workspace / ".git"
    (git_dir / "objects").mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
    yield workspace


@pytest.mark.asyncio
async def test_ensure_workspace_fetches_origin_on_healthy_short_circuit(
    healthy_workspace: Path,
) -> None:
    """Healthy-clone re-entry must invoke `git fetch origin`.

    Without this, a respawned PM/Doc reads stale refs and reviews a diff
    that no longer matches the dev's pushed branch.
    """
    svc = _service()
    agent = _fake_agent()
    _bind(svc, "_lookup_agent_or_raise", AsyncMock(return_value=agent))
    _bind(svc, "get_workspace_path", MagicMock(return_value=healthy_workspace))

    captured: list[list[str]] = []

    def _fake_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        captured.append(args)
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        )

    with (
        patch("roboco.services.workspace.subprocess.run", side_effect=_fake_run),
        patch("roboco.services.workspace._ensure_agent_owned"),
    ):
        result = await svc.ensure_workspace(
            project_slug="roboco",
            agent_id=agent.id,
        )

    assert result == healthy_workspace
    fetch_calls = [
        a
        for a in captured
        if len(a) >= _MIN_GIT_FETCH_ARGC and a[0] == "git" and "fetch" in a
    ]
    assert fetch_calls, (
        f"Expected `git fetch origin` on healthy short-circuit, "
        f"got subprocess calls: {captured}"
    )
    # Specifically: `git fetch origin` (no extra positional refspec — fetch
    # all branches' refs so PM/Doc sees every dev branch).
    assert any(a[-2:] == ["fetch", "origin"] for a in fetch_calls), (
        f"Expected exact `git fetch origin`, got: {fetch_calls}"
    )


@pytest.mark.asyncio
async def test_ensure_workspace_fetch_failure_does_not_abort(
    healthy_workspace: Path,
) -> None:
    """Fetch is best-effort: a non-zero return code logs but does NOT raise.

    Network blips and offline-mode CI must not break workspace setup.
    """
    svc = _service()
    agent = _fake_agent()
    _bind(svc, "_lookup_agent_or_raise", AsyncMock(return_value=agent))
    _bind(svc, "get_workspace_path", MagicMock(return_value=healthy_workspace))

    def _fake_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            args=args, returncode=128, stdout="", stderr="fatal: unable to access"
        )

    with (
        patch("roboco.services.workspace.subprocess.run", side_effect=_fake_run),
        patch("roboco.services.workspace._ensure_agent_owned"),
    ):
        result = await svc.ensure_workspace(
            project_slug="roboco",
            agent_id=agent.id,
        )

    assert result == healthy_workspace
