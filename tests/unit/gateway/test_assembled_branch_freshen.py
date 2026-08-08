"""Behind-base auto-sync for the assembled PM submits (B2).

The needs_revision ↔ awaiting_pr_review ping-pong (live, 2026-07-02): a cell /
root revision re-submitted a head whose BASE had moved (sibling cells merged),
so the gate re-failed the same missing-work finding every cycle. Leaf devs
have the ``_behind_base_gate`` + ``sync_branch``; the assembled submits had no
freshness check at all. ``_freshen_assembled_branch`` closes that: at
submit_up / submit_root time every child is terminal, so rebasing the
assembled branch onto its base is safe — conflicts become a clean rejection
naming the files instead of a blind re-review.

``_freshen_assembled_branch`` returns ``(rejection_envelope, ahead)`` — the
``ahead`` half lets the caller reuse this probe's own ``is_behind_base``
fetch for the downstream PR-waiver check instead of fetching origin again
(see ``test_verb_runner.py``'s ``precomputed_ahead`` coverage).
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
    fresh_ahead_count = 3
    git = AsyncMock()
    git.is_behind_base.return_value = (0, fresh_ahead_count)
    c = Choreographer(_make_deps(git=git))
    env, ahead = await c._freshen_assembled_branch(
        _cell_task(), base_branch="feature/main_pm/root", verb="submit_up"
    )
    assert env is None
    # Up to date, no rebase — the probe's own ahead count is trustworthy and
    # reusable by the downstream PR-waiver check.
    assert ahead == fresh_ahead_count
    git.sync_task_branch.assert_not_awaited()


@pytest.mark.asyncio
async def test_freshen_rebases_when_behind_and_proceeds() -> None:
    git = AsyncMock()
    git.is_behind_base.return_value = (2, 3)
    git.sync_task_branch.return_value = {"status": "rebased", "unique_commits": 3}
    c = Choreographer(_make_deps(git=git))
    env, ahead = await c._freshen_assembled_branch(
        _cell_task(), base_branch="feature/main_pm/root", verb="submit_up"
    )
    assert env is None
    # A rebase ran — the pre-rebase ahead count is not reused; the caller
    # must fetch fresh.
    assert ahead is None
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
    env, ahead = await c._freshen_assembled_branch(
        _cell_task(), base_branch="feature/main_pm/root", verb="submit_up"
    )
    assert env is not None
    assert ahead is None
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "stats.json" in body["message"]


@pytest.mark.asyncio
async def test_freshen_diverged_rejects() -> None:
    """A diverged branch is refused just like a conflict — never guessed at."""
    git = AsyncMock()
    git.is_behind_base.return_value = (2, 3)
    git.sync_task_branch.return_value = {
        "status": "diverged",
        "local_only": 1,
        "origin_only": 2,
    }
    c = Choreographer(_make_deps(git=git))
    env, ahead = await c._freshen_assembled_branch(
        _cell_task(), base_branch="feature/main_pm/root", verb="submit_up"
    )
    assert env is not None
    assert ahead is None
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "DIVERGED" in body["message"]
    assert "reconcile" in body["remediate"]


@pytest.mark.asyncio
async def test_freshen_fails_open_on_probe_error() -> None:
    git = AsyncMock()
    git.is_behind_base.side_effect = RuntimeError("network sad")
    c = Choreographer(_make_deps(git=git))
    env, ahead = await c._freshen_assembled_branch(
        _cell_task(), base_branch="feature/main_pm/root", verb="submit_up"
    )
    assert env is None
    assert ahead is None


@pytest.mark.asyncio
async def test_freshen_fails_open_on_sync_error() -> None:
    git = AsyncMock()
    git.is_behind_base.return_value = (1, 1)
    git.sync_task_branch.side_effect = RuntimeError("rebase runner sad")
    c = Choreographer(_make_deps(git=git))
    env, ahead = await c._freshen_assembled_branch(
        _cell_task(), base_branch="feature/main_pm/root", verb="submit_up"
    )
    assert env is None
    assert ahead is None


@pytest.mark.asyncio
async def test_freshen_skips_branchless_and_missing_base() -> None:
    git = AsyncMock()
    c = Choreographer(_make_deps(git=git))
    branchless = MagicMock(id=uuid4(), branch_name=None, team="frontend")
    assert await c._freshen_assembled_branch(
        branchless, base_branch="x", verb="submit_up"
    ) == (None, None)
    assert await c._freshen_assembled_branch(
        _cell_task(), base_branch="", verb="submit_up"
    ) == (None, None)
    git.is_behind_base.assert_not_awaited()
