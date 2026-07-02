"""Assembly integrity at submit_up — every completed child's work must be in
the assembled branch (incident #11).

Live break (2026-07-02, S6 cell PR #183): revert subtask 3b9cc162 COMPLETED,
but its commit never landed on the assembled cell branch — the reviewer
re-flagged the exact violation the revert fixed, spawning another revision
cycle. The gate verifies patch-equivalence (rebase-safe) per completed child
and refuses the submit naming the children whose work is missing.
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
        id=uuid4(), branch_name="feature/frontend/root--cell", team="frontend"
    )


@pytest.mark.asyncio
async def test_integrity_passes_when_all_children_merged() -> None:
    git = AsyncMock()
    git.unmerged_child_commits.return_value = []
    c = Choreographer(_make_deps(git=git))
    env = await c._assembly_integrity_guard(_cell_task(), verb="submit_up")
    assert env is None


@pytest.mark.asyncio
async def test_integrity_rejects_naming_missing_children() -> None:
    git = AsyncMock()
    git.unmerged_child_commits.return_value = [
        {
            "task_id": "3b9cc162",
            "title": "Revert stats.json artifact",
            "unmerged": 1,
        }
    ]
    c = Choreographer(_make_deps(git=git))
    env = await c._assembly_integrity_guard(_cell_task(), verb="submit_up")
    assert env is not None
    body = env.as_dict()
    assert body["error"] == "invalid_state"
    assert "Revert stats.json artifact" in body["message"]


@pytest.mark.asyncio
async def test_integrity_fails_open_on_git_error() -> None:
    git = AsyncMock()
    git.unmerged_child_commits.side_effect = RuntimeError("git sad")
    c = Choreographer(_make_deps(git=git))
    env = await c._assembly_integrity_guard(_cell_task(), verb="submit_up")
    assert env is None


@pytest.mark.asyncio
async def test_integrity_skips_branchless_task() -> None:
    git = AsyncMock()
    c = Choreographer(_make_deps(git=git))
    t = MagicMock(id=uuid4(), branch_name=None)
    env = await c._assembly_integrity_guard(t, verb="submit_up")
    assert env is None
    git.unmerged_child_commits.assert_not_awaited()
