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

# Healthy short-circuit chowns BEFORE fetch (repair pre-existing root
# ownership) AND AFTER fetch (repair root-owned pack/refs the fetch
# just wrote). Named to satisfy ruff PLR2004.
_MIN_CHOWN_CALLS_AROUND_FETCH = 2


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
    # Specifically: a SCOPED `git fetch --no-tags --prune origin <ref...>` with
    # NO `-c` flag. The fetch is scoped to the workspace's branches (current +
    # default) rather than all refs so it can't time out on a monorepo with many
    # accumulated feature/* branches. The `-c` check protects the docstring's
    # no-token-injection invariant — a future refactor that added
    # `git -c http.extraheader=...` must not slip in unnoticed.
    assert any(
        a[0] == "git"
        and "-c" not in a
        and "fetch" in a
        and "--no-tags" in a
        and "--prune" in a
        and "origin" in a
        and a.index("origin") < len(a) - 1  # ≥1 ref after origin → scoped
        for a in fetch_calls
    ), f"Expected scoped `git fetch --no-tags --prune origin <ref>`, got: {fetch_calls}"


@pytest.mark.asyncio
async def test_refresh_fetch_is_scoped_to_current_and_default_branch(
    healthy_workspace: Path,
) -> None:
    """The refresh fetch targets only the current branch + default, not all refs.

    An all-refs fetch times out on a monorepo with many accumulated feature/*
    branches, leaving the workspace silently stale.
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
        out = ""
        if "rev-parse" in args:
            out = "feature/frontend/abc12345"
        elif "symbolic-ref" in args:
            out = "origin/master"
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout=out, stderr=""
        )

    with (
        patch("roboco.services.workspace.subprocess.run", side_effect=_fake_run),
        patch("roboco.services.workspace._ensure_agent_owned"),
    ):
        await svc.ensure_workspace(project_slug="roboco", agent_id=agent.id)

    fetch = next(a for a in captured if a[0] == "git" and "fetch" in a)
    after_origin = fetch[fetch.index("origin") + 1 :]
    assert "feature/frontend/abc12345" in after_origin, (
        f"current branch must be fetched, got: {fetch}"
    )
    assert "master" in after_origin, f"default branch must be fetched, got: {fetch}"


@pytest.mark.asyncio
async def test_ensure_workspace_rechowns_after_refresh_fetch(
    healthy_workspace: Path,
) -> None:
    """Healthy-clone re-entry MUST chown again AFTER `git fetch origin`.

    The orchestrator runs as root, so `git fetch` writes new pack files
    under `.git/objects/pack/` and updates refs under
    `.git/refs/remotes/origin/` — those land root-owned, undoing the
    pre-fetch chown. Without a post-fetch chown, the next agent-side
    write (.git/index.lock, packed-refs, etc.) hits Permission denied.

    This mirrors the pattern in `fetch_branch_for_inspection`.
    """
    svc = _service()
    agent = _fake_agent()
    _bind(svc, "_lookup_agent_or_raise", AsyncMock(return_value=agent))
    _bind(svc, "get_workspace_path", MagicMock(return_value=healthy_workspace))

    call_log: list[str] = []

    def _fake_run(
        args: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        if "fetch" in args:
            call_log.append("fetch")
        return subprocess.CompletedProcess(
            args=args, returncode=0, stdout="", stderr=""
        )

    def _fake_chown(_workspace: object) -> None:
        call_log.append("chown")

    with (
        patch("roboco.services.workspace.subprocess.run", side_effect=_fake_run),
        patch(
            "roboco.services.workspace._ensure_agent_owned",
            side_effect=_fake_chown,
        ),
    ):
        await svc.ensure_workspace(
            project_slug="roboco",
            agent_id=agent.id,
        )

    # Expect at least: chown (pre-fetch) -> fetch -> chown (post-fetch).
    # The post-fetch chown is the load-bearing one — it repairs ownership
    # of objects/refs the root-side fetch just wrote.
    assert call_log.count("chown") >= _MIN_CHOWN_CALLS_AROUND_FETCH, (
        f"Expected at least two chown calls (pre + post fetch), got: {call_log}"
    )
    fetch_idx = call_log.index("fetch")
    assert "chown" in call_log[fetch_idx + 1 :], (
        f"Expected a chown AFTER the fetch, got: {call_log}"
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
