"""Wave C2 (2026-05-12): 30s TTL cache on ensure_workspace refresh fetch.

Smoke run 3 fired 'ensure_workspace: refresh fetch returned non-zero'
9 times per run because each evidence(task_id) call triggered
ensure_workspace, which fetches even when the workspace was just
fetched seconds ago. The TTL prevents redundant work.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch
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


# Named constants to satisfy ruff PLR2004 (magic value in comparison).
_FETCH_TTL_SECONDS = 30.0
_EXPECTED_ONE_FETCH = 1
_EXPECTED_TWO_FETCHES = 2


@pytest.mark.asyncio
async def test_second_fetch_within_ttl_is_skipped(
    healthy_workspace: Path,
) -> None:
    """ensure_workspace within 30s of a successful fetch skips the second fetch."""
    svc = _service()
    agent = _fake_agent()
    _bind(svc, "_lookup_agent_or_raise", AsyncMock(return_value=agent))
    _bind(svc, "get_workspace_path", MagicMock(return_value=healthy_workspace))

    fetch_call_count = 0

    async def fake_fetch(_workspace: Path, _project_slug: str) -> None:
        nonlocal fetch_call_count
        fetch_call_count += 1

    with (
        patch.object(
            WorkspaceService,
            "_fetch_origin_best_effort",
            side_effect=fake_fetch,
        ),
        patch("roboco.services.workspace._ensure_agent_owned"),
    ):
        await svc.ensure_workspace(project_slug="roboco", agent_id=agent.id)
        await svc.ensure_workspace(project_slug="roboco", agent_id=agent.id)

    assert fetch_call_count == _EXPECTED_ONE_FETCH, (
        f"second ensure_workspace within TTL should skip fetch; "
        f"got {fetch_call_count} fetches"
    )


@pytest.mark.asyncio
async def test_fetch_after_ttl_runs_again(
    healthy_workspace: Path,
) -> None:
    """After 30s, the cache expires and the next ensure_workspace fetches again."""
    svc = _service()
    agent = _fake_agent()
    _bind(svc, "_lookup_agent_or_raise", AsyncMock(return_value=agent))
    _bind(svc, "get_workspace_path", MagicMock(return_value=healthy_workspace))

    fetch_call_count = 0

    async def fake_fetch(_workspace: Path, _project_slug: str) -> None:
        nonlocal fetch_call_count
        fetch_call_count += 1

    # Simulate time advancing past the TTL between the two calls.
    # _monotonic() is called twice per ensure_workspace invocation when a
    # fetch runs: once to read "now" and once to stamp the cache after the
    # fetch. Sequence:
    #   call 1 (now, 1st ensure_workspace): 0.0   -> fetch runs
    #   call 2 (stamp, 1st ensure_workspace): 0.0  -> cache set to 0.0
    #   call 3 (now, 2nd ensure_workspace): 31.0   -> TTL expired -> fetch runs
    #   call 4 (stamp, 2nd ensure_workspace): 31.0 -> cache set to 31.0
    monotonic_calls = [0.0, 0.0, _FETCH_TTL_SECONDS + 1, _FETCH_TTL_SECONDS + 1]

    with (
        patch.object(
            WorkspaceService,
            "_fetch_origin_best_effort",
            side_effect=fake_fetch,
        ),
        patch("roboco.services.workspace._ensure_agent_owned"),
        patch(
            "roboco.services.workspace._monotonic",
            side_effect=monotonic_calls,
        ),
    ):
        await svc.ensure_workspace(project_slug="roboco", agent_id=agent.id)
        await svc.ensure_workspace(project_slug="roboco", agent_id=agent.id)

    assert fetch_call_count == _EXPECTED_TWO_FETCHES, (
        f"after TTL expiry the second ensure_workspace should fetch again; "
        f"got {fetch_call_count} fetches"
    )


@pytest.mark.asyncio
async def test_force_true_bypasses_cache(
    healthy_workspace: Path,
) -> None:
    """ensure_workspace(force=True) fetches even if the cache is fresh."""
    svc = _service()
    agent = _fake_agent()
    _bind(svc, "_lookup_agent_or_raise", AsyncMock(return_value=agent))
    _bind(svc, "get_workspace_path", MagicMock(return_value=healthy_workspace))

    fetch_call_count = 0

    async def fake_fetch(_workspace: Path, _project_slug: str) -> None:
        nonlocal fetch_call_count
        fetch_call_count += 1

    with (
        patch.object(
            WorkspaceService,
            "_fetch_origin_best_effort",
            side_effect=fake_fetch,
        ),
        patch("roboco.services.workspace._ensure_agent_owned"),
    ):
        # First call populates the cache.
        await svc.ensure_workspace(project_slug="roboco", agent_id=agent.id)
        # Second call with force=True must bypass the cache and fetch again.
        await svc.ensure_workspace(
            project_slug="roboco", agent_id=agent.id, force=True
        )

    assert fetch_call_count == _EXPECTED_TWO_FETCHES, (
        f"force=True should bypass cache and fetch again; "
        f"got {fetch_call_count} fetches"
    )


@pytest.mark.asyncio
async def test_different_workspaces_have_independent_caches(
    tmp_path: Path,
) -> None:
    """The TTL cache is per workspace path — fetching workspace A does not
    suppress the fetch for workspace B even within 30s."""

    def _make_healthy(slug: str) -> Path:
        workspace = tmp_path / "roboco" / "backend" / slug
        git_dir = workspace / ".git"
        (git_dir / "objects").mkdir(parents=True)
        (git_dir / "HEAD").write_text("ref: refs/heads/main\n")
        return workspace

    workspace_a = _make_healthy("agent-a")
    workspace_b = _make_healthy("agent-b")

    svc = _service()

    fetched_paths: list[str] = []

    async def fake_fetch(workspace: Path, _project_slug: str) -> None:
        fetched_paths.append(str(workspace))

    agent_a = _fake_agent("agent-a")
    agent_b = _fake_agent("agent-b")

    with (
        patch.object(
            WorkspaceService,
            "_fetch_origin_best_effort",
            side_effect=fake_fetch,
        ),
        patch("roboco.services.workspace._ensure_agent_owned"),
    ):
        _bind(svc, "_lookup_agent_or_raise", AsyncMock(return_value=agent_a))
        _bind(svc, "get_workspace_path", MagicMock(return_value=workspace_a))
        await svc.ensure_workspace(project_slug="roboco", agent_id=agent_a.id)

        _bind(svc, "_lookup_agent_or_raise", AsyncMock(return_value=agent_b))
        _bind(svc, "get_workspace_path", MagicMock(return_value=workspace_b))
        await svc.ensure_workspace(project_slug="roboco", agent_id=agent_b.id)

    assert len(fetched_paths) == _EXPECTED_TWO_FETCHES, (
        f"both workspaces should be fetched independently; "
        f"got {len(fetched_paths)} fetches: {fetched_paths}"
    )
    assert str(workspace_a) in fetched_paths
    assert str(workspace_b) in fetched_paths
