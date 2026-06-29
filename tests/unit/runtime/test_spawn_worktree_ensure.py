"""Spawn-time worktree ensure (F123, Phase B).

A respawn re-points the container ``-w`` at the task's worktree. If the
worktree was pruned while the agent was down, ``docker run -w <missing>`` starts
the agent in a non-existent directory and its first command fails. So the
worktree must be re-attached (idempotent) BEFORE the container launches.
``_ensure_worktree_before_spawn`` is the chokepoint; it is a no-op for
branchless / no-task spawns (no worktree).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.models.runtime import SpawnGitContext
from roboco.runtime.orchestrator import AgentOrchestrator, AgentReadinessError
from roboco.services.workspace import WorkspaceError


def _make_orchestrator() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._bg_tasks = set()
    orch._running = True
    return orch


@asynccontextmanager
async def _fake_db_ctx(db: Any) -> Any:
    yield db


@pytest.mark.asyncio
async def test_ensures_worktree_when_task_short_id_set() -> None:
    orch = _make_orchestrator()
    ctx = SpawnGitContext(
        project_slug="roboco-api",
        branch_name="feature/backend/abc12345",
        task_short_id="a3c40fe7",
    )
    db = MagicMock()
    ws = MagicMock()
    ws.ensure_worktree_for_resume = AsyncMock()

    with (
        patch("roboco.db.base.get_db_context", return_value=_fake_db_ctx(db)),
        patch("roboco.services.workspace.WorkspaceService", return_value=ws),
    ):
        await orch._ensure_worktree_before_spawn(
            ctx, "roboco-api", "backend", "be-dev-1", "task-1"
        )

    ws.ensure_worktree_for_resume.assert_awaited_once()
    args = ws.ensure_worktree_for_resume.call_args.args
    assert args[0] == Path("/data/workspaces/roboco-api/backend/be-dev-1")
    assert args[1] == Path(
        "/data/workspaces/roboco-api/backend/be-dev-1/.worktrees/a3c40fe7"
    )
    assert args[2] == "feature/backend/abc12345"


@pytest.mark.asyncio
async def test_noop_when_no_task_short_id() -> None:
    # A branchless / no-task spawn has no worktree — must not touch the FS.
    orch = _make_orchestrator()
    ctx = SpawnGitContext(project_slug="roboco-api", branch_name=None)

    db = MagicMock()
    ws = MagicMock()
    with (
        patch("roboco.db.base.get_db_context", return_value=_fake_db_ctx(db)),
        patch("roboco.services.workspace.WorkspaceService", return_value=ws),
    ):
        await orch._ensure_worktree_before_spawn(
            ctx, "roboco-api", "backend", "be-dev-1", "task-1"
        )

    ws.ensure_worktree_for_resume.assert_not_called()


@pytest.mark.asyncio
async def test_fatal_failure_releases_claim_and_aborts() -> None:
    # A FATAL git-state failure (WorkspaceError — the branch ref is gone, so the
    # worktree cannot be re-added) must NOT launch the container at a missing
    # -w path. It releases the claim (so the next claim rebuilds the worktree
    # via create_branch) and aborts the spawn with AgentReadinessError.
    orch = _make_orchestrator()
    release = AsyncMock()
    object.__setattr__(orch, "_release_claim_to_pending", release)
    task_id = str(uuid4())
    ctx = SpawnGitContext(
        project_slug="roboco-api",
        branch_name="feature/backend/abc12345",
        task_short_id="a3c40fe7",
    )
    ws = MagicMock()
    ws.ensure_worktree_for_resume = MagicMock(
        side_effect=WorkspaceError("git worktree re-add failed")
    )

    with (
        patch("roboco.db.base.get_db_context", return_value=_fake_db_ctx(MagicMock())),
        patch("roboco.services.workspace.WorkspaceService", return_value=ws),
        pytest.raises(AgentReadinessError, match="worktree ensure failed"),
    ):
        await orch._ensure_worktree_before_spawn(
            ctx, "roboco-api", "backend", "be-dev-1", task_id
        )

    release.assert_awaited_once_with(task_id)


@pytest.mark.asyncio
async def test_transient_failure_aborts_without_release() -> None:
    # A TRANSIENT failure (DB hiccup / other) must still abort (don't launch at
    # a possibly-missing path) but must NOT release the claim — a fresh claim
    # would not help and re-cloning is destructive. Next tick retries the same
    # claim.
    orch = _make_orchestrator()
    release = AsyncMock()
    object.__setattr__(orch, "_release_claim_to_pending", release)
    task_id = str(uuid4())
    ctx = SpawnGitContext(
        project_slug="roboco-api",
        branch_name="feature/backend/abc12345",
        task_short_id="a3c40fe7",
    )
    ws = MagicMock()
    ws.ensure_worktree_for_resume = MagicMock(side_effect=RuntimeError("db down"))

    with (
        patch("roboco.db.base.get_db_context", return_value=_fake_db_ctx(MagicMock())),
        patch("roboco.services.workspace.WorkspaceService", return_value=ws),
        pytest.raises(AgentReadinessError, match="transient"),
    ):
        await orch._ensure_worktree_before_spawn(
            ctx, "roboco-api", "backend", "be-dev-1", task_id
        )

    release.assert_not_awaited()


@pytest.mark.asyncio
async def test_recoverable_ensure_no_raise_no_release() -> None:
    # The happy / recoverable path (worktree present, or pruned-but-re-added
    # from the surviving branch ref) must stay a silent no-op — that is the
    # F123 Phase B happy path. No raise, no claim release.
    orch = _make_orchestrator()
    release = AsyncMock()
    object.__setattr__(orch, "_release_claim_to_pending", release)
    ctx = SpawnGitContext(
        project_slug="roboco-api",
        branch_name="feature/backend/abc12345",
        task_short_id="a3c40fe7",
    )
    ws = MagicMock()
    ws.ensure_worktree_for_resume = AsyncMock()  # succeeds

    with (
        patch("roboco.db.base.get_db_context", return_value=_fake_db_ctx(MagicMock())),
        patch("roboco.services.workspace.WorkspaceService", return_value=ws),
    ):
        await orch._ensure_worktree_before_spawn(
            ctx, "roboco-api", "backend", "be-dev-1", str(uuid4())
        )

    release.assert_not_awaited()
