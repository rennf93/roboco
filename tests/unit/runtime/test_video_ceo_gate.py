"""Video-post drafts are CEO-gated artifacts, never delivery work; the video
authoring task is normal, dispatched work.

Mirrors test_self_heal_ceo_gate.py / test_x_dispatch_skip.py across the three
held-source skip sites: ``_is_held_ceo_source`` (used by ``_dispatch_pm_
work``), ``_dispatch_dev_work``'s inline skip chain, and ``TaskService.
list_pending_for_agent``'s SQL-level gate. A ``video_post`` task must never
reach any of the three; ``video`` (the UX/UI authoring task) must reach all of
them exactly like any other pre-assigned code task.
"""

from __future__ import annotations

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.foundation import identity as _foundation
from roboco.runtime.orchestrator import AgentOrchestrator, _is_held_ceo_source
from roboco.services.task import VIDEO_POST_SOURCE, VIDEO_SOURCE, TaskService
from sqlalchemy.dialects import postgresql

UX_DEV_UUID = _foundation.AGENTS["ux-dev-1"].uuid


def _task(tid: str, source: str, *, assigned_to: str | None = None) -> dict[str, Any]:
    return {"id": tid, "source": source, "assigned_to": assigned_to}


def _bind(svc: object, name: str, value: object) -> None:
    """Stub `name` on `svc` without tripping mypy's method-assign check."""
    object.__setattr__(svc, name, value)


# ---------------------------------------------------------------------------
# _is_held_ceo_source: the direct predicate
# ---------------------------------------------------------------------------


def test_is_held_ceo_source_true_for_video_post() -> None:
    assert _is_held_ceo_source({"source": VIDEO_POST_SOURCE}) is True


def test_is_held_ceo_source_false_for_video_authoring() -> None:
    assert _is_held_ceo_source({"source": VIDEO_SOURCE}) is False


# ---------------------------------------------------------------------------
# _dispatch_pm_work: a video_post draft is never routed as PM delivery work
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_video_post_never_routed_by_pm_dispatch() -> None:
    tasks = [
        _task("A", VIDEO_POST_SOURCE, assigned_to="secretary-1"),
        _task("B", VIDEO_SOURCE, assigned_to="ux-dev-1"),
        _task("C", "manual"),  # ordinary unassigned -> routing still happens
    ]
    stub = MagicMock()
    stub._fetch_tasks = AsyncMock(return_value=tasks)
    stub._is_task_handled_this_tick = MagicMock(return_value=False)
    stub._resolve_agent_slug = MagicMock(return_value="ux-dev-1")
    stub._BOARD_AGENTS = frozenset()
    stub._route_unassigned_pm_task = AsyncMock()
    stub._handle_pm_assigned_task = AsyncMock()
    stub._handle_board_assigned_task = AsyncMock()

    client: Any = MagicMock()
    await AgentOrchestrator._dispatch_pm_work(cast("AgentOrchestrator", stub), client)

    handled = [c.args[0]["id"] for c in stub._handle_pm_assigned_task.await_args_list]
    assert handled == ["B"]  # the authoring task dispatches; the held draft doesn't
    stub._handle_board_assigned_task.assert_not_awaited()
    routed = [c.args[1]["id"] for c in stub._route_unassigned_pm_task.await_args_list]
    assert routed == ["C"]


# ---------------------------------------------------------------------------
# _dispatch_dev_work: a video_post draft is never routed as dev work
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_video_post_never_routed_by_dev_dispatch() -> None:
    tasks = [
        _task("A", VIDEO_POST_SOURCE, assigned_to="secretary-1"),
        _task("B", VIDEO_SOURCE, assigned_to="ux-dev-1"),
    ]
    stub = MagicMock()
    stub._fetch_tasks = AsyncMock(return_value=tasks)
    stub._is_task_handled_this_tick = MagicMock(return_value=False)
    stub._dev_dispatch_one = AsyncMock()

    client: Any = MagicMock()
    await AgentOrchestrator._dispatch_dev_work(cast("AgentOrchestrator", stub), client)

    handled = [c.args[1]["id"] for c in stub._dev_dispatch_one.await_args_list]
    assert handled == ["B"]  # only the authoring task reaches dev dispatch


# ---------------------------------------------------------------------------
# list_pending_for_agent: give_me_work never offers a held video_post task
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_pending_for_agent_excludes_held_video_post() -> None:
    """Asserted at the SQL layer, mirroring the self-heal regression guard: the
    query scopes the hold to video_post (among others), so the database drops
    a held video-post draft before the agent ever sees the list."""
    session = MagicMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    session.execute = AsyncMock(return_value=result)
    svc = TaskService(session)
    _bind(svc, "unmet_dependency_ids", AsyncMock(return_value=[]))

    await svc.list_pending_for_agent(UX_DEV_UUID)

    stmt = session.execute.await_args.args[0]
    compiled = str(
        stmt.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert VIDEO_POST_SOURCE in compiled
    assert "confirmed_by_human" in compiled


@pytest.mark.asyncio
async def test_list_pending_for_agent_still_offers_video_authoring_task() -> None:
    """Regression guard: the hold is scoped to VIDEO_POST_SOURCE, not the
    video-authoring source. A pre-assigned, confirmed video task (the normal
    shape VideoEngine.open_video_task creates) must still be offered."""
    session = MagicMock()

    authoring = MagicMock()
    authoring.source = VIDEO_SOURCE
    authoring.confirmed_by_human = True
    authoring.dependency_ids = []

    result = MagicMock()
    result.scalars.return_value.all.return_value = [authoring]
    session.execute = AsyncMock(return_value=result)
    svc = TaskService(session)
    _bind(svc, "unmet_dependency_ids", AsyncMock(return_value=[]))

    available = await svc.list_pending_for_agent(UX_DEV_UUID)

    assert authoring in available


# ---------------------------------------------------------------------------
# _check_dev_needs_subtasks: the authoring task must dispatch, not deadlock
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_video_authoring_shape_passes_dev_subtask_guard() -> None:
    """A video authoring task is a root task assigned to a dev, so it must be
    LOW complexity to clear _check_dev_needs_subtasks. A medium/high root dev
    task is auto-blocked for subtasks it will never own, and unblock only loops
    it back to a re-block — a permanent deadlock. Guards the shape
    VideoEngine.open_video_task emits."""
    stub = MagicMock()
    stub._auto_block_task = AsyncMock()
    task = {
        "id": "vid-low",
        "source": VIDEO_SOURCE,
        "assigned_to": "ux-dev-1",
        "estimated_complexity": "low",
        "parent_task_id": None,
    }
    client: Any = MagicMock()
    result = await AgentOrchestrator._check_dev_needs_subtasks(
        cast("AgentOrchestrator", stub), client, task
    )
    assert result is None
    stub._auto_block_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_medium_root_dev_task_is_blocked() -> None:
    """Documents why the authoring task must stay LOW: a medium root task with
    no subtasks assigned to a dev IS blocked here — the exact trap a regression
    back to medium/high complexity would spring."""
    stub = MagicMock()
    stub._auto_block_task = AsyncMock()
    stub._api_url = "http://orchestrator"
    resp = MagicMock()
    resp.is_success = True
    resp.json.return_value = []
    client: Any = MagicMock()
    client.get = AsyncMock(return_value=resp)
    task = {
        "id": "vid-med",
        "source": VIDEO_SOURCE,
        "assigned_to": "ux-dev-1",
        "estimated_complexity": "medium",
        "parent_task_id": None,
    }
    result = await AgentOrchestrator._check_dev_needs_subtasks(
        cast("AgentOrchestrator", stub), client, task
    )
    assert result is not None
    stub._auto_block_task.assert_awaited_once()


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
