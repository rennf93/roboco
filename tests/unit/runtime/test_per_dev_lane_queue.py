"""Per-dev sequenced queues: a dev works its own code queue one task at a time.

A PM delegates a full per-dev queue of `code` subtasks up front. The dispatch
barrier holds a dev's higher-sequence code leaf until its own lower-sequence
code siblings under the same parent are terminal, so the dev works its queue in
order — while the OTHER dev's lane runs concurrently (two-dev parallelism).
Keyed on the assignee (not the team like the merge barrier) and gates only
`code`. Loop-free (not dispatched, not rejected); best-effort on lookup failure.
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
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    cast("Any", orch)._pm_respawn_tracker = {}
    cast("Any", orch)._schedule_respawn_persist = lambda *_a, **_k: None
    return orch


def _sibling(
    seq: int,
    owner: str,
    status: TaskStatus,
    *,
    task_type: str = "code",
) -> MagicMock:
    return MagicMock(
        id=uuid4(),
        sequence=seq,
        assigned_to=owner,
        status=status,
        task_type=task_type,
    )


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


def _task(seq: int, owner: str, *, task_type: str = "code") -> dict[str, Any]:
    return {
        "id": str(uuid4()),
        "parent_task_id": str(uuid4()),
        "sequence": seq,
        "assigned_to": owner,
        "task_type": task_type,
    }


@pytest.mark.asyncio
async def test_blocks_when_same_dev_has_earlier_active_code_sibling() -> None:
    orch = _new_orchestrator()
    task = _task(1, "be-dev-1")
    siblings = [_sibling(0, "be-dev-1", TaskStatus.IN_PROGRESS)]
    p1, p2 = _patch_siblings(siblings)
    with p1, p2:
        assert await orch._blocked_by_earlier_lane_sibling(task) is True


@pytest.mark.asyncio
async def test_not_blocked_when_earlier_same_dev_sibling_terminal() -> None:
    orch = _new_orchestrator()
    task = _task(1, "be-dev-1")
    siblings = [
        _sibling(0, "be-dev-1", TaskStatus.COMPLETED),
        _sibling(0, "be-dev-1", TaskStatus.CANCELLED),
    ]
    p1, p2 = _patch_siblings(siblings)
    with p1, p2:
        assert await orch._blocked_by_earlier_lane_sibling(task) is False


@pytest.mark.asyncio
async def test_other_devs_earlier_sibling_does_not_block() -> None:
    """The whole point of two-dev parallelism: be-dev-2's in-flight wave-0 leaf
    must NOT hold be-dev-1's own wave-0 leaf. Lanes are independent."""
    orch = _new_orchestrator()
    task = _task(0, "be-dev-1")
    siblings = [_sibling(0, "be-dev-2", TaskStatus.IN_PROGRESS)]
    p1, p2 = _patch_siblings(siblings)
    with p1, p2:
        assert await orch._blocked_by_earlier_lane_sibling(task) is False


@pytest.mark.asyncio
async def test_earlier_non_code_sibling_does_not_block() -> None:
    """Only the code queue is gated this way; a planning/doc sibling is irrelevant."""
    orch = _new_orchestrator()
    task = _task(1, "be-dev-1")
    siblings = [_sibling(0, "be-dev-1", TaskStatus.IN_PROGRESS, task_type="planning")]
    p1, p2 = _patch_siblings(siblings)
    with p1, p2:
        assert await orch._blocked_by_earlier_lane_sibling(task) is False


@pytest.mark.asyncio
async def test_higher_sequence_same_dev_sibling_does_not_block() -> None:
    orch = _new_orchestrator()
    task = _task(0, "be-dev-1")
    siblings = [_sibling(1, "be-dev-1", TaskStatus.IN_PROGRESS)]
    p1, p2 = _patch_siblings(siblings)
    with p1, p2:
        assert await orch._blocked_by_earlier_lane_sibling(task) is False


@pytest.mark.asyncio
async def test_equal_sequence_same_dev_tiebreaks_by_created_at() -> None:
    """Wave ties in a dev's own lane order by created_at (mirroring the merge
    barrier): the earlier-created tied sibling holds the later one; the
    later-created one does not hold the earlier."""
    orch = _new_orchestrator()
    task = _task(0, "be-dev-1")
    task["created_at"] = "2026-07-10T12:00:00+00:00"
    earlier = _sibling(0, "be-dev-1", TaskStatus.IN_PROGRESS)
    earlier.created_at = datetime(2026, 7, 10, 11, 0, tzinfo=UTC)
    p1, p2 = _patch_siblings([earlier])
    with p1, p2:
        assert await orch._blocked_by_earlier_lane_sibling(task) is True

    later = _sibling(0, "be-dev-1", TaskStatus.IN_PROGRESS)
    later.created_at = datetime(2026, 7, 10, 13, 0, tzinfo=UTC)
    p1, p2 = _patch_siblings([later])
    with p1, p2:
        assert await orch._blocked_by_earlier_lane_sibling(task) is False


@pytest.mark.asyncio
async def test_non_code_task_is_never_gated_without_db() -> None:
    orch = _new_orchestrator()
    # A planning/doc task short-circuits before any lookup.
    task = _task(1, "fe-pm", task_type="planning")
    assert await orch._blocked_by_earlier_lane_sibling(task) is False


@pytest.mark.asyncio
async def test_no_parent_or_owner_short_circuits_without_db() -> None:
    orch = _new_orchestrator()
    assert (
        await orch._blocked_by_earlier_lane_sibling(
            {"id": str(uuid4()), "sequence": 0, "task_type": "code"}
        )
        is False
    )


@pytest.mark.asyncio
async def test_db_failure_falls_through_to_dispatch() -> None:
    orch = _new_orchestrator()
    task = _task(1, "be-dev-1")
    boom = patch(
        "roboco.db.base.get_session_factory", side_effect=RuntimeError("db down")
    )
    with boom:
        assert await orch._blocked_by_earlier_lane_sibling(task) is False


@pytest.mark.asyncio
async def test_spawn_pending_dev_holds_gated_lane_before_validating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_spawn_pending_dev must short-circuit a gated lane before validating or
    spawning — the dev's earlier queue item is still live."""
    orch = _new_orchestrator()
    task = _task(1, "be-dev-1")
    spawn = AsyncMock()
    validate = AsyncMock()
    monkeypatch.setattr(orch, "_is_agent_active", MagicMock(return_value=False))
    monkeypatch.setattr(
        orch, "_blocked_by_earlier_lane_sibling", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(orch, "_validate_task_for_spawn", validate)
    monkeypatch.setattr(orch, "spawn_agent", spawn)

    await orch._spawn_pending_dev(cast("Any", MagicMock()), task, "be-dev-1")

    spawn.assert_not_awaited()
    validate.assert_not_awaited()


@pytest.mark.asyncio
async def test_spawn_pending_dev_proceeds_when_lane_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the lane is clear (no earlier sibling), the dev is spawned normally."""
    orch = _new_orchestrator()
    task = _task(0, "be-dev-1")
    spawn = AsyncMock()
    monkeypatch.setattr(orch, "_is_agent_active", MagicMock(return_value=False))
    monkeypatch.setattr(
        orch, "_blocked_by_earlier_lane_sibling", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(orch, "_validate_task_for_spawn", AsyncMock(return_value=None))
    monkeypatch.setattr(orch, "spawn_agent", spawn)
    monkeypatch.setattr(orch, "_get_prompt_for_agent", AsyncMock(return_value="prompt"))
    monkeypatch.setattr(orch, "_task_git_context", MagicMock(return_value={}))

    await orch._spawn_pending_dev(cast("Any", MagicMock()), task, "be-dev-1")

    spawn.assert_awaited_once()
