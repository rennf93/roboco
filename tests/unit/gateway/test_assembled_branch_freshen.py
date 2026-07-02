"""Behind-base auto-sync for the assembled PM submits (B2).

The needs_revision ↔ awaiting_pr_review ping-pong (live, 2026-07-02): a cell /
root revision re-submitted a head whose BASE had moved (sibling cells merged),
so the gate re-failed the same missing-work finding every cycle. Leaf devs
have the ``_behind_base_gate`` + ``sync_branch``; the assembled submits had no
freshness check at all. ``_freshen_assembled_branch`` closes that: at
submit_up / submit_root time every child is terminal, so rebasing the
assembled branch onto its base is safe — conflicts become a clean rejection
naming the files instead of a blind re-review.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps


def _make_deps(**overrides: Any) -> ChoreographerDeps:
    base: dict[str, Any] = {
        "task": AsyncMock(),
        "work_session": AsyncMock(),
        "git": AsyncMock(),
        "a2a": AsyncMock(),
        "journal": AsyncMock(),
        "audit": AsyncMock(),
        "evidence_repo": AsyncMock(),
    }
    base.update(overrides)
    return ChoreographerDeps(**base)


def _cell_task() -> MagicMock:
    return MagicMock(
        id=uuid4(),
        branch_name="feature/frontend/root--cell",
        team="frontend",
    )


@pytest.mark.asyncio
async def test_freshen_noop_when_up_to_date() -> None:
    git = AsyncMock()
    git.is_behind_base.return_value = (0, 3)
    c = Choreographer(_make_deps(git=git))
    env = await c._freshen_assembled_branch(
        _cell_task(), base_branch="feature/main_pm/root", verb="submit_up"
    )
    assert env is None
    git.sync_task_branch.assert_not_awaited()


@pytest.mark.asyncio
async def test_freshen_rebases_when_behind_and_proceeds() -> None:
    git = AsyncMock()
    git.is_behind_base.return_value = (2, 3)
    git.sync_task_branch.return_value = {"status": "rebased", "unique_commits": 3}
    c = Choreographer(_make_deps(git=git))
    env = await c._freshen_assembled_branch(
        _cell_task(), base_branch="feature/main_pm/root", verb="submit_up"
    )
    assert env is None
    git.sync_task_branch.assert_awaited_once()


@pytest.mark.asyncio
async def test_freshen_conflicts_reject_with_files() -> None:
    git = AsyncMock()
    git.is_behind_base.return_value = (2, 3)
    git.sync_task_branch.return_value = {
        "status": "conflicts",
        "files": ["frontend/src/lib/stats.json"],
    }
    c = Choreographer(_make_deps(git=git))
    env = await c._freshen_assembled_branch(
        _cell_task(), base_branch="feature/main_pm/root", verb="submit_up"
    )
    assert env is not None
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "stats.json" in body["message"]


@pytest.mark.asyncio
async def test_freshen_fails_open_on_probe_error() -> None:
    git = AsyncMock()
    git.is_behind_base.side_effect = RuntimeError("network sad")
    c = Choreographer(_make_deps(git=git))
    env = await c._freshen_assembled_branch(
        _cell_task(), base_branch="feature/main_pm/root", verb="submit_up"
    )
    assert env is None


@pytest.mark.asyncio
async def test_freshen_fails_open_on_sync_error() -> None:
    git = AsyncMock()
    git.is_behind_base.return_value = (1, 1)
    git.sync_task_branch.side_effect = RuntimeError("rebase runner sad")
    c = Choreographer(_make_deps(git=git))
    env = await c._freshen_assembled_branch(
        _cell_task(), base_branch="feature/main_pm/root", verb="submit_up"
    )
    assert env is None


@pytest.mark.asyncio
async def test_freshen_skips_branchless_and_missing_base() -> None:
    git = AsyncMock()
    c = Choreographer(_make_deps(git=git))
    branchless = MagicMock(id=uuid4(), branch_name=None, team="frontend")
    assert (
        await c._freshen_assembled_branch(branchless, base_branch="x", verb="submit_up")
        is None
    )
    assert (
        await c._freshen_assembled_branch(
            _cell_task(), base_branch="", verb="submit_up"
        )
        is None
    )
    git.is_behind_base.assert_not_awaited()
