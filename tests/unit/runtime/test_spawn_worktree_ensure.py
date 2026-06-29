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
from unittest.mock import MagicMock, patch

import pytest
from roboco.models.runtime import SpawnGitContext
from roboco.runtime.orchestrator import AgentOrchestrator


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
    ws.ensure_worktree_for_resume = MagicMock()

    with (
        patch("roboco.db.base.get_db_context", return_value=_fake_db_ctx(db)),
        patch("roboco.services.workspace.WorkspaceService", return_value=ws),
    ):
        await orch._ensure_worktree_before_spawn(
            ctx, "roboco-api", "backend", "be-dev-1"
        )

    ws.ensure_worktree_for_resume.assert_called_once()
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
            ctx, "roboco-api", "backend", "be-dev-1"
        )

    ws.ensure_worktree_for_resume.assert_not_called()


@pytest.mark.asyncio
async def test_failure_does_not_raise() -> None:
    # Best-effort: a worktree-ensure failure (broken clone, DB hiccup) must
    # NOT block the spawn — the agent can still start and GitService re-clones
    # on first op. It only warns.
    orch = _make_orchestrator()
    ctx = SpawnGitContext(
        project_slug="roboco-api",
        branch_name="feature/backend/abc12345",
        task_short_id="a3c40fe7",
    )
    ws = MagicMock()
    ws.ensure_worktree_for_resume = MagicMock(side_effect=RuntimeError("boom"))

    with (
        patch("roboco.db.base.get_db_context", return_value=_fake_db_ctx(MagicMock())),
        patch("roboco.services.workspace.WorkspaceService", return_value=ws),
    ):
        # Must not raise.
        await orch._ensure_worktree_before_spawn(
            ctx, "roboco-api", "backend", "be-dev-1"
        )
