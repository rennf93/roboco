"""Unit tests for TaskService's durable stalled-marker methods.

Backs the orchestrator's respawn breaker (see
tests/unit/runtime/test_stalled_marker.py for the orchestrator-side
behavior) and the `GET` stalled-set read endpoint.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.models.base import StalledReason
from roboco.services.task import StalledTaskEntry, TaskService

if TYPE_CHECKING:
    from roboco.db.tables import TaskTable


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
async def test_emit_status_transition_audit_clears_stalled_marker() -> None:
    """A task's stalled marker (set when the respawn breaker trips) must
    clear on ANY status transition, not only when the same (agent, task)
    dispatcher tracker key happens to re-observe a status change. That
    narrower dispatcher-side path (``_respawn_status_change_resets`` ->
    ``AgentOrchestrator._clear_task_stalled_marker``) never re-fires once a
    dev's task advances past the dispatcher's own fetch scope — e.g. a
    stalled dev task recovering and moving from ``in_progress`` to
    ``awaiting_qa`` — which used to leave a resolved task looking
    permanently stalled until it went terminal. The single
    status-transition chokepoint now clears it unconditionally regardless
    of which key or path drove the transition."""
    session = MagicMock()
    session.add = MagicMock()
    svc = TaskService(session)

    task = SimpleNamespace(
        id=uuid4(),
        team=SimpleNamespace(value="backend"),
        claimed_by=None,
        revision_count=0,
        stalled_reason=StalledReason.BREAKER_TRIPPED.value,
        stalled_since=datetime.now(UTC),
        pr_number=None,
        pr_url=None,
    )

    svc._emit_status_transition_audit(
        cast("TaskTable", task),
        from_status="in_progress",
        to_status="awaiting_qa",
        agent_role="developer",
        audit_agent_id=None,
    )

    assert task.stalled_reason is None
    assert task.stalled_since is None


@pytest.mark.asyncio
async def test_emit_status_transition_audit_no_op_when_not_stalled() -> None:
    """A task with no marker set stays untouched (no accidental writes)."""
    session = MagicMock()
    session.add = MagicMock()
    svc = TaskService(session)

    task = SimpleNamespace(
        id=uuid4(),
        team=SimpleNamespace(value="backend"),
        claimed_by=None,
        revision_count=0,
        stalled_reason=None,
        stalled_since=None,
        pr_number=None,
        pr_url=None,
    )

    svc._emit_status_transition_audit(
        cast("TaskTable", task),
        from_status="pending",
        to_status="in_progress",
        agent_role="developer",
        audit_agent_id=None,
    )

    assert task.stalled_reason is None
    assert task.stalled_since is None


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
    CANCELLED must never leak back into the stalled set. This is now a
    defensive backstop — ``_emit_status_transition_audit`` clears the marker
    on every transition — but pin the exclusion at the query level too: the
    compiled SQL must filter out both terminal statuses, since the
    mocked-session tests above can't exercise real row filtering."""
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
