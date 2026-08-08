"""Unit tests for TaskService's durable stalled-marker methods.

Backs the orchestrator's respawn breaker (see
tests/unit/runtime/test_stalled_marker.py for the orchestrator-side
behavior) and the `GET` stalled-set read endpoint.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.models.base import StalledReason
from roboco.services.task import StalledTaskEntry, TaskService


def _service_with(execute_returns: object) -> TaskService:
    session = MagicMock()
    session.execute = AsyncMock(return_value=execute_returns)
    return TaskService(session)


@pytest.mark.asyncio
async def test_mark_stalled_sets_reason_and_since() -> None:
    task_id = uuid4()
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock())
    svc = TaskService(session)

    await svc.mark_stalled(task_id, reason=StalledReason.BREAKER_TRIPPED.value)

    session.execute.assert_awaited_once()
    stmt = session.execute.await_args.args[0]
    rendered = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "stalled_reason" in rendered
    assert "stalled_since" in rendered
    assert "breaker_tripped" in rendered
    assert task_id.hex in rendered


@pytest.mark.asyncio
async def test_clear_stalled_marker_conditions_on_reason_set() -> None:
    """Only writes when stalled_reason is already set (skips the common case)."""
    task_id = uuid4()
    session = MagicMock()
    session.execute = AsyncMock(return_value=MagicMock())
    svc = TaskService(session)

    await svc.clear_stalled_marker(task_id)

    stmt = session.execute.await_args.args[0]
    rendered = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "stalled_reason IS NOT NULL" in rendered
    assert task_id.hex in rendered


@pytest.mark.asyncio
async def test_list_stalled_tasks_maps_rows_and_computes_duration() -> None:
    task_id = uuid4()
    assignee_id = uuid4()
    stalled_hours = 2
    since = datetime.now(UTC) - timedelta(hours=stalled_hours)
    row = SimpleNamespace(
        id=task_id,
        title="Wedged task",
        assigned_to=assignee_id,
        slug="be-dev-1",
        status=SimpleNamespace(value="in_progress"),
        stalled_reason=StalledReason.BREAKER_TRIPPED.value,
        stalled_since=since,
    )
    result = MagicMock()
    result.all = MagicMock(return_value=[row])
    svc = _service_with(result)

    entries = await svc.list_stalled_tasks()

    assert entries == [
        StalledTaskEntry(
            task_id=task_id,
            title="Wedged task",
            assignee_id=assignee_id,
            assignee_slug="be-dev-1",
            status="in_progress",
            reason=StalledReason.BREAKER_TRIPPED.value,
            stalled_since=since,
            stalled_seconds=entries[0].stalled_seconds,
        )
    ]
    # Duration should match the seeded stalled_hours, with slack for test runtime.
    expected_seconds = stalled_hours * 3600
    slack_seconds = 100
    assert abs(entries[0].stalled_seconds - expected_seconds) < slack_seconds


@pytest.mark.asyncio
async def test_list_stalled_tasks_excludes_terminal_statuses_at_query_level() -> None:
    """A stalled marker set on a task that later reaches COMPLETED or
    CANCELLED must never leak back into the stalled set — the only clear
    path (AgentOrchestrator._clear_task_stalled_marker) runs solely from the
    dispatcher's re-observation branch, which never fires for a terminal
    task. Pin the exclusion at the query level: the compiled SQL must filter
    out both terminal statuses, since the mocked-session tests above can't
    exercise real row filtering."""
    result = MagicMock()
    result.all = MagicMock(return_value=[])
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    svc = TaskService(session)

    await svc.list_stalled_tasks()

    stmt = session.execute.await_args.args[0]
    rendered = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "status NOT IN" in rendered
    assert "'completed'" in rendered
    assert "'cancelled'" in rendered


@pytest.mark.asyncio
async def test_list_stalled_tasks_empty_when_none_stalled() -> None:
    result = MagicMock()
    result.all = MagicMock(return_value=[])
    svc = _service_with(result)

    entries = await svc.list_stalled_tasks()

    assert entries == []
