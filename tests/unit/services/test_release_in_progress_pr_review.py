"""``i_am_idle`` releases an in_progress PR-review task instead of pausing it.

A reviewer cannot resume a paused task, so the pause stranded the review lane.
``release_review_claims`` sends the task back to PENDING through the existing
``unclaim_for_agent`` path; a developer's in_progress task is not selected.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.task import PR_REVIEW_SOURCES, TaskService


def _service(
    rows: list[SimpleNamespace], monkeypatch: pytest.MonkeyPatch
) -> tuple[TaskService, MagicMock]:
    session = MagicMock()
    session.flush = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = rows
    session.execute = AsyncMock(return_value=result)
    svc = TaskService(session)
    monkeypatch.setattr(svc, "list_open_review_claims", AsyncMock(return_value=[]))
    return svc, session


@pytest.mark.asyncio
async def test_in_progress_pr_review_goes_back_to_pending_unclaimed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    task = SimpleNamespace(
        id=uuid4(), claimed_by=agent_id, claimed_at=object(), last_heartbeat_at=object()
    )
    svc, _ = _service([task], monkeypatch)
    unclaim = AsyncMock(return_value=task)
    monkeypatch.setattr(svc, "unclaim_for_agent", unclaim)

    released = await svc.release_review_claims(agent_id)

    assert released == [task.id]
    unclaim.assert_awaited_once_with(task.id, agent_id)
    assert task.claimed_by is None
    assert task.claimed_at is None
    assert task.last_heartbeat_at is None


@pytest.mark.asyncio
async def test_refused_unclaim_is_not_reported_released(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent_id = uuid4()
    task = SimpleNamespace(id=uuid4(), claimed_by=agent_id)
    svc, _ = _service([task], monkeypatch)
    monkeypatch.setattr(svc, "unclaim_for_agent", AsyncMock(return_value=None))

    assert await svc.release_review_claims(agent_id) == []
    assert task.claimed_by == agent_id


@pytest.mark.asyncio
async def test_query_selects_only_in_progress_pr_review_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A developer's in_progress task (source manual) is outside the query, so
    it still goes through the auto-pause."""
    svc, session = _service([], monkeypatch)

    await svc.release_review_claims(uuid4())

    sql = str(session.execute.await_args.args[0])
    assert "tasks.source IN" in sql
    assert "tasks.status =" in sql
    assert set(PR_REVIEW_SOURCES) == {"external_pr", "internal_pr"}
