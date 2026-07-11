"""Sequence-ordered merge: hold a later sibling's review until earlier ones land.

Leaf siblings share one cell branch, so merging a higher-sequence sibling before
a lower one diverges the branch and wedges the loser. The dispatcher skips a
higher-sequence task while an earlier same-team sibling is still non-terminal —
loop-free (not dispatched, not rejected). Terminal siblings never block, so a
cancelled sibling can't deadlock the rest.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.models.base import TaskStatus
from roboco.runtime.orchestrator import AgentOrchestrator


def _new_orchestrator() -> AgentOrchestrator:
    return AgentOrchestrator.__new__(AgentOrchestrator)


def _sibling(seq: int, team: str, status: TaskStatus) -> MagicMock:
    return MagicMock(id=uuid4(), sequence=seq, team=team, status=status)


def _patch_siblings(siblings: list[MagicMock]) -> Any:
    """Patch the orchestrator's direct-DB sibling lookup to return ``siblings``."""
    svc = MagicMock()
    svc.get_subtasks = AsyncMock(return_value=siblings)

    class _CM:
        async def __aenter__(self) -> MagicMock:
            return MagicMock()

        async def __aexit__(self, *_a: Any) -> bool:
            return False

    factory = MagicMock(return_value=_CM())
    return (
        patch("roboco.db.base.get_session_factory", return_value=factory),
        patch("roboco.services.task.get_task_service", return_value=svc),
    )


@pytest.mark.asyncio
async def test_blocks_when_earlier_same_team_sibling_active() -> None:
    orch = _new_orchestrator()
    task = {
        "id": str(uuid4()),
        "parent_task_id": str(uuid4()),
        "sequence": 1,
        "team": "frontend",
    }
    siblings = [_sibling(0, "frontend", TaskStatus.IN_PROGRESS)]
    p1, p2 = _patch_siblings(siblings)
    with p1, p2:
        assert await orch._blocked_by_earlier_sibling(task) is True


@pytest.mark.asyncio
async def test_not_blocked_when_earlier_sibling_terminal() -> None:
    orch = _new_orchestrator()
    task = {
        "id": str(uuid4()),
        "parent_task_id": str(uuid4()),
        "sequence": 1,
        "team": "frontend",
    }
    # Earlier sibling completed -> no longer blocks. (Cancelled likewise.)
    siblings = [
        _sibling(0, "frontend", TaskStatus.COMPLETED),
        _sibling(0, "frontend", TaskStatus.CANCELLED),
    ]
    p1, p2 = _patch_siblings(siblings)
    with p1, p2:
        assert await orch._blocked_by_earlier_sibling(task) is False


@pytest.mark.asyncio
async def test_not_blocked_by_different_team_sibling() -> None:
    orch = _new_orchestrator()
    task = {
        "id": str(uuid4()),
        "parent_task_id": str(uuid4()),
        "sequence": 1,
        "team": "frontend",
    }
    # A backend sibling targets a different branch — never blocks the frontend leaf.
    siblings = [_sibling(0, "backend", TaskStatus.IN_PROGRESS)]
    p1, p2 = _patch_siblings(siblings)
    with p1, p2:
        assert await orch._blocked_by_earlier_sibling(task) is False


@pytest.mark.asyncio
async def test_higher_sequence_sibling_does_not_block() -> None:
    orch = _new_orchestrator()
    task = {
        "id": str(uuid4()),
        "parent_task_id": str(uuid4()),
        "sequence": 0,
        "team": "frontend",
    }
    # A LATER sibling (seq 1) must not hold up the earlier one (seq 0).
    siblings = [_sibling(1, "frontend", TaskStatus.IN_PROGRESS)]
    p1, p2 = _patch_siblings(siblings)
    with p1, p2:
        assert await orch._blocked_by_earlier_sibling(task) is False


@pytest.mark.asyncio
async def test_equal_sequence_created_earlier_sibling_blocks() -> None:
    """Wave ties (independent siblings share a sequence) tie-break by
    created_at: the earlier-created same-team sibling merges first, so the
    later one's review dispatch is held — the shared-cell-branch merge race
    the ordinal used to prevent."""
    orch = _new_orchestrator()
    task = {
        "id": str(uuid4()),
        "parent_task_id": str(uuid4()),
        "sequence": 0,
        "team": "frontend",
        "created_at": "2026-07-10T12:00:00+00:00",
    }
    earlier = _sibling(0, "frontend", TaskStatus.IN_PROGRESS)
    earlier.created_at = datetime(2026, 7, 10, 11, 0, tzinfo=UTC)
    p1, p2 = _patch_siblings([earlier])
    with p1, p2:
        assert await orch._blocked_by_earlier_sibling(task) is True


@pytest.mark.asyncio
async def test_equal_sequence_created_later_sibling_does_not_block() -> None:
    orch = _new_orchestrator()
    task = {
        "id": str(uuid4()),
        "parent_task_id": str(uuid4()),
        "sequence": 0,
        "team": "frontend",
        "created_at": "2026-07-10T12:00:00+00:00",
    }
    later = _sibling(0, "frontend", TaskStatus.IN_PROGRESS)
    later.created_at = datetime(2026, 7, 10, 13, 0, tzinfo=UTC)
    p1, p2 = _patch_siblings([later])
    with p1, p2:
        assert await orch._blocked_by_earlier_sibling(task) is False


@pytest.mark.asyncio
async def test_equal_sequence_unparseable_created_at_fails_open() -> None:
    """A tie that can't be ordered (missing/mock created_at) must not wedge
    dispatch — the tiebreak degrades to not-blocked."""
    orch = _new_orchestrator()
    task = {
        "id": str(uuid4()),
        "parent_task_id": str(uuid4()),
        "sequence": 0,
        "team": "frontend",
    }
    tie = _sibling(0, "frontend", TaskStatus.IN_PROGRESS)
    p1, p2 = _patch_siblings([tie])
    with p1, p2:
        assert await orch._blocked_by_earlier_sibling(task) is False


@pytest.mark.asyncio
async def test_no_parent_returns_false_without_db() -> None:
    orch = _new_orchestrator()
    task = {"id": str(uuid4()), "sequence": 0, "team": "frontend"}
    # No DB patch: a parentless task must short-circuit before any lookup.
    assert await orch._blocked_by_earlier_sibling(task) is False


@pytest.mark.asyncio
async def test_db_failure_falls_through_to_dispatch() -> None:
    orch = _new_orchestrator()
    task = {
        "id": str(uuid4()),
        "parent_task_id": str(uuid4()),
        "sequence": 1,
        "team": "frontend",
    }
    boom = patch(
        "roboco.db.base.get_session_factory", side_effect=RuntimeError("db down")
    )
    with boom:
        # The ordering check must never wedge the dispatcher: degrade to dispatch.
        assert await orch._blocked_by_earlier_sibling(task) is False


@pytest.mark.asyncio
async def test_dispatch_skips_blocked_sibling(monkeypatch: pytest.MonkeyPatch) -> None:
    """_dispatch_pm_review_work must not spawn a PM for a gated task."""
    orch = _new_orchestrator()
    task = {
        "id": str(uuid4()),
        "parent_task_id": str(uuid4()),
        "sequence": 1,
        "team": "frontend",
        "assigned_to": str(uuid4()),
    }
    spawn = AsyncMock()
    # monkeypatch.setattr keeps mypy's method-assign check satisfied without
    # silencing it; the spawn mock is held locally so the assertion is typed.
    monkeypatch.setattr(orch, "_fetch_tasks", AsyncMock(return_value=[task]))
    monkeypatch.setattr(
        orch, "_blocked_by_earlier_sibling", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(orch, "spawn_agent", spawn)
    monkeypatch.setattr(orch, "_resolve_agent_slug", MagicMock(return_value="fe-pm"))
    monkeypatch.setattr(orch, "_is_agent_active", MagicMock(return_value=False))

    await orch._dispatch_pm_review_work(cast("Any", MagicMock()))

    spawn.assert_not_awaited()
