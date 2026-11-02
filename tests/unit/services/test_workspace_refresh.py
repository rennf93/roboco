"""Unit tests for `WorkspaceService.ensure_workspace` refresh behavior.

Audit H26: when a PM/Doc is respawned and re-enters `ensure_workspace`
on an already-healthy clone, the previous implementation short-circuited
with no fetch — leaving the agent looking at arbitrarily stale refs.
These tests pin the new behavior: every healthy short-circuit MUST run
`git fetch origin` (best-effort), served through the project's relay.
"""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.workspace import WorkspaceService

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


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


def _wire_healthy_reentry(
    svc: WorkspaceService, workspace: Path, *, fetch_returncode: int = 0
) -> AsyncMock:
    """Stub everything around the refresh fetch; return the origin stub, which
    records the ``origin`` command the relay is asked to serve."""
    agent = _fake_agent()
    _bind(svc, "_lookup_agent_or_raise", AsyncMock(return_value=agent))
    _bind(svc, "get_workspace_path", MagicMock(return_value=workspace))
    _bind(svc, "_prune_broken_refs", MagicMock())
    _bind(svc, "install_dev_deps", AsyncMock())

    def _local(
        _ws: Path, args: list[str], **_kw: Any
    ) -> subprocess.CompletedProcess[str]:
        out = ""
        if args[0] == "rev-parse":
            out = "feature/frontend/abc12345\n"
        elif args[0] == "symbolic-ref":
            out = "origin/master\n"
        return subprocess.CompletedProcess(args, 0, out, "")

    _bind(svc, "_worktree_git", MagicMock(side_effect=_local))
    origin = AsyncMock(
        return_value=subprocess.CompletedProcess(
            ["git"], fetch_returncode, "", "fatal: unable to access"
        )
    )
    _bind(svc, "_origin_git", origin)
    return origin


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
    origin = _wire_healthy_reentry(svc, healthy_workspace)

    result = await svc.ensure_workspace(project_slug="roboco", agent_id=uuid4())

    assert result == healthy_workspace
    origin.assert_awaited_once()
    assert origin.await_args is not None
    repo, project_slug, args = origin.await_args.args
    assert (repo, project_slug) == (healthy_workspace, "roboco")
    # A SCOPED `fetch --no-tags --prune origin <ref...>`: scoped to the
    # workspace's branches (current + default) rather than all refs so it
    # can't time out on a monorepo with many accumulated feature/* branches.
    # The command names `origin` only — the relay owns the URL and token.
    assert args[:4] == ["fetch", "--no-tags", "--prune", "origin"]
    assert len(args) > len(["fetch", "--no-tags", "--prune", "origin"])


@pytest.mark.asyncio
async def test_refresh_fetch_is_scoped_to_current_and_default_branch(
    healthy_workspace: Path,
) -> None:
    """The refresh fetch targets only the current branch + default, not all refs.

    An all-refs fetch times out on a monorepo with many accumulated feature/*
    branches, leaving the workspace silently stale.
    """
    svc = _service()
    origin = _wire_healthy_reentry(svc, healthy_workspace)

    await svc.ensure_workspace(project_slug="roboco", agent_id=uuid4())

    assert origin.await_args is not None
    args = origin.await_args.args[2]
    assert args[args.index("origin") + 1 :] == ["feature/frontend/abc12345", "master"]


@pytest.mark.asyncio
async def test_ensure_workspace_fetch_failure_does_not_abort(
    healthy_workspace: Path,
) -> None:
    """Fetch is best-effort: a non-zero return code logs but does NOT raise.

    Network blips and offline-mode CI must not break workspace setup.
    """
    svc = _service()
    _wire_healthy_reentry(svc, healthy_workspace, fetch_returncode=128)

    result = await svc.ensure_workspace(project_slug="roboco", agent_id=uuid4())

    assert result == healthy_workspace
