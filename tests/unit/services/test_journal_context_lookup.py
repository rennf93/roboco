"""Smoke-5: get_journal_context_task_for_agent includes BLOCKED, PAUSED,
NEEDS_REVISION so note/say/dm/notify auto-inject task_id while the agent
is stuck.

Original bug: get_active_task_for_agent filtered to DEV_ACTIVE statuses only.
PMs writing decisions during BLOCKED state got task_id=NULL on their journal
entries. The C8 tracing gate then never found the decisions and the agent
spiraled. Smoke-5 wrote 5 decisions, 8 reflections, 1 struggle — all with
task_id=NULL because the agent was stuck when journaling.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.models.base import TaskStatus
from roboco.services.task import TaskService


def _service_with(execute_returns: object) -> TaskService:
    session = MagicMock()
    session.execute = AsyncMock(return_value=execute_returns)
    return TaskService(session)


@pytest.mark.asyncio
async def test_journal_context_returns_blocked_task() -> None:
    """A BLOCKED task is returned by the journal-context lookup."""
    task = MagicMock(id=uuid4(), status=TaskStatus.BLOCKED)
    result = MagicMock()
    result.scalar_one_or_none.return_value = task
    svc = _service_with(result)
    found = await svc.get_journal_context_task_for_agent(uuid4())
    assert found is task


@pytest.mark.asyncio
async def test_journal_context_returns_paused_task() -> None:
    """A PAUSED task is returned by the journal-context lookup."""
    task = MagicMock(id=uuid4(), status=TaskStatus.PAUSED)
    result = MagicMock()
    result.scalar_one_or_none.return_value = task
    svc = _service_with(result)
    found = await svc.get_journal_context_task_for_agent(uuid4())
    assert found is task


@pytest.mark.asyncio
async def test_journal_context_returns_needs_revision_task() -> None:
    """A NEEDS_REVISION task is returned so QA-rejected devs still get task_id."""
    task = MagicMock(id=uuid4(), status=TaskStatus.NEEDS_REVISION)
    result = MagicMock()
    result.scalar_one_or_none.return_value = task
    svc = _service_with(result)
    found = await svc.get_journal_context_task_for_agent(uuid4())
    assert found is task


@pytest.mark.asyncio
async def test_journal_context_query_filters_by_journal_statuses() -> None:
    """The query's where clause uses _JOURNAL_CONTEXT_STATUSES, not _DEV_ACTIVE."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    svc = TaskService(session)
    agent_id = uuid4()

    await svc.get_journal_context_task_for_agent(agent_id)

    assert session.execute.await_count == 1
    sent_query = session.execute.await_args.args[0]
    rendered = str(sent_query.compile(compile_kwargs={"literal_binds": True}))
    for s in ("blocked", "paused", "needs_revision", "in_progress", "claimed"):
        assert s in rendered, (
            f"Journal-context query missing status {s}. Rendered SQL: {rendered}"
        )


@pytest.mark.asyncio
async def test_dev_active_query_still_excludes_blocked() -> None:
    """The narrow get_active_task_for_agent stays narrow — commit() relies on it."""
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    svc = TaskService(session)

    await svc.get_active_task_for_agent(uuid4())

    sent_query = session.execute.await_args.args[0]
    rendered = str(sent_query.compile(compile_kwargs={"literal_binds": True}))
    assert "'blocked'" not in rendered, (
        "Dev-active query must NOT include BLOCKED — commit() would otherwise "
        "allow commits from a blocked task."
    )
    assert "'paused'" not in rendered
    assert "'needs_revision'" not in rendered
