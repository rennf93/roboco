"""#173: progress % is derived from the plan checklist, not agent-set.

The plan's sub_tasks ARE the progress skeleton. progress(plan_step=...)
marks that step completed and the percentage is computed from
completed/total (equal weight) — the agent cannot game it. Narrative
entries (no plan_step) carry the current derived %. Tasks with no
sub_task checklist fall back to the supplied percentage (back-compat).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.task import TaskService

_PCT_HALF = 50
_PCT_FULL = 100
_PCT_NONE = 0
_PCT_FALLBACK = 42


def _svc_with_task(task: Any) -> TaskService:
    svc = TaskService.__new__(TaskService)
    svc.get = AsyncMock(return_value=task)  # type: ignore[method-assign]
    svc.session = MagicMock()
    svc.session.flush = AsyncMock()
    return svc


def _task_with_plan(sub_tasks: list[dict[str, Any]]) -> MagicMock:
    return MagicMock(
        id=uuid4(),
        plan={"text": "p", "sub_tasks": sub_tasks},
        progress_updates=[],
    )


@pytest.mark.asyncio
async def test_marking_step_by_id_derives_percentage() -> None:
    sid = str(uuid4())
    task = _task_with_plan(
        [
            {"id": sid, "title": "A", "completed": False},
            {"id": str(uuid4()), "title": "B", "completed": False},
        ]
    )
    svc = _svc_with_task(task)
    agent = uuid4()

    res = await svc.record_plan_progress(task.id, agent, "did A", plan_step=sid)
    assert res is not None
    assert res["step_resolved"] is True
    assert res["percentage"] == _PCT_HALF  # 1 of 2
    assert task.plan["sub_tasks"][0]["completed"] is True
    assert task.progress_updates[-1]["percentage"] == _PCT_HALF
    assert task.progress_updates[-1]["message"] == "did A"


@pytest.mark.asyncio
async def test_marking_step_by_one_based_order() -> None:
    task = _task_with_plan(
        [
            {"id": "x", "title": "A", "completed": True},
            {"id": "y", "title": "B", "completed": False},
        ]
    )
    svc = _svc_with_task(task)
    res = await svc.record_plan_progress(task.id, uuid4(), "did B", plan_step="2")
    assert res["step_resolved"] is True
    assert res["percentage"] == _PCT_FULL  # both now complete
    assert task.plan["sub_tasks"][1]["completed"] is True


@pytest.mark.asyncio
async def test_unknown_step_is_not_resolved_and_lists_valid() -> None:
    task = _task_with_plan([{"id": "s1", "title": "A", "completed": False}])
    svc = _svc_with_task(task)
    res = await svc.record_plan_progress(task.id, uuid4(), "?", plan_step="nope")
    assert res["step_resolved"] is False
    assert res["valid_steps"] == ["s1"]
    # Nothing marked; % still derived from (unchanged) checklist = 0.
    assert task.plan["sub_tasks"][0]["completed"] is False
    assert res["percentage"] == _PCT_NONE


@pytest.mark.asyncio
async def test_narrative_entry_carries_current_derived_pct() -> None:
    task = _task_with_plan(
        [
            {"id": "a", "title": "A", "completed": True},
            {"id": "b", "title": "B", "completed": False},
        ]
    )
    svc = _svc_with_task(task)
    res = await svc.record_plan_progress(task.id, uuid4(), "midway note")
    assert res["step_resolved"] is None  # no plan_step requested
    assert res["percentage"] == _PCT_HALF  # current checklist state
    assert task.progress_updates[-1]["message"] == "midway note"


@pytest.mark.asyncio
async def test_no_checklist_falls_back_to_supplied_percentage() -> None:
    task = MagicMock(id=uuid4(), plan="just a string plan", progress_updates=[])
    svc = _svc_with_task(task)
    res = await svc.record_plan_progress(
        task.id, uuid4(), "legacy", fallback_percentage=_PCT_FALLBACK
    )
    assert res["percentage"] == _PCT_FALLBACK
    assert res["valid_steps"] == []
    assert task.progress_updates[-1]["percentage"] == _PCT_FALLBACK


@pytest.mark.asyncio
async def test_missing_task_returns_none() -> None:
    svc = TaskService.__new__(TaskService)
    svc.get = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert await svc.record_plan_progress(uuid4(), uuid4(), "x") is None
