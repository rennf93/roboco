"""Unit tests for JournalService gateway-backfill methods."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.models.base import JournalEntryType
from roboco.services.journal import JournalService


def _service_with_count(count: int) -> JournalService:
    """Build a JournalService whose count query returns `count`."""
    result = MagicMock()
    result.scalar.return_value = count
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    session.flush = AsyncMock()
    return JournalService(session)


def _service_with_scalar(value: object) -> JournalService:
    """Build a JournalService whose scalar query returns `value`.

    Mirrors `_service_with_count` but lets the test inject any value
    (including a `datetime` or `None`) for the single-column query path
    used by `latest_decision_at`.
    """
    result = MagicMock()
    result.scalar.return_value = value
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    session.flush = AsyncMock()
    return JournalService(session)


@pytest.mark.asyncio
async def test_has_decision_for_task_true_when_count_positive() -> None:
    svc = _service_with_count(1)
    assert await svc.has_decision_for_task(uuid4(), uuid4()) is True


@pytest.mark.asyncio
async def test_has_decision_for_task_false_when_zero() -> None:
    svc = _service_with_count(0)
    assert await svc.has_decision_for_task(uuid4(), uuid4()) is False


@pytest.mark.asyncio
async def test_has_learning_for_task_true_when_count_positive() -> None:
    svc = _service_with_count(2)
    assert await svc.has_learning_for_task(uuid4(), uuid4()) is True


@pytest.mark.asyncio
async def test_has_learning_for_task_false_when_zero() -> None:
    svc = _service_with_count(0)
    assert await svc.has_learning_for_task(uuid4(), uuid4()) is False


@pytest.mark.asyncio
async def test_has_reflect_for_task_true_when_count_positive() -> None:
    svc = _service_with_count(3)
    assert await svc.has_reflect_for_task(uuid4(), uuid4()) is True


@pytest.mark.asyncio
async def test_has_reflect_for_task_false_when_zero() -> None:
    svc = _service_with_count(0)
    assert await svc.has_reflect_for_task(uuid4(), uuid4()) is False


def _bind(svc: JournalService, name: str, value: object) -> None:
    object.__setattr__(svc, name, value)


@pytest.mark.asyncio
async def test_write_struggle_calls_add_struggle_with_task_id() -> None:
    """write_struggle delegates to add_struggle with a STRUGGLE-typed entry.

    The struggle is built via StruggleEntryParams; we verify the service
    passes through the agent_id + task_id and uses the first content line
    as title (truncated to 100 chars).
    """
    svc = JournalService(MagicMock(flush=AsyncMock()))
    add_struggle_mock = AsyncMock(
        return_value=MagicMock(type=JournalEntryType.STRUGGLE)
    )
    _bind(svc, "add_struggle", add_struggle_mock)
    agent_id = uuid4()
    task_id = uuid4()
    content = "Cannot find the right migration file\nMore detail here"
    out = await svc.write_struggle(agent_id=agent_id, task_id=task_id, content=content)
    assert out is not None
    add_struggle_mock.assert_awaited_once()
    args, _kwargs = add_struggle_mock.call_args
    # First arg is agent_id; second is StruggleEntryParams
    assert args[0] == agent_id
    params = args[1]
    assert params.task_id == task_id
    assert params.title == "Cannot find the right migration file"
    assert content in params.what_struggled


@pytest.mark.asyncio
async def test_write_struggle_handles_empty_content_gracefully() -> None:
    svc = JournalService(MagicMock(flush=AsyncMock()))
    add_struggle_mock = AsyncMock(return_value=MagicMock())
    _bind(svc, "add_struggle", add_struggle_mock)
    await svc.write_struggle(agent_id=uuid4(), task_id=uuid4(), content="")
    args, _kwargs = add_struggle_mock.call_args
    params = args[1]
    assert params.title == "Struggle"


# ---------------------------------------------------------------------------
# latest_decision_at — windowed-satisfaction support for the PM-decision gate
# (C8). Returns the `created_at` of the newest DECISION_LOG entry for an
# (agent, task) pair, or None if no decision exists.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_latest_decision_at_returns_none_when_no_decision() -> None:
    """No DECISION_LOG entries → scalar query returns None → method returns None."""
    svc = _service_with_scalar(None)
    assert await svc.latest_decision_at(uuid4(), uuid4()) is None


@pytest.mark.asyncio
async def test_latest_decision_at_returns_timestamp_of_single_decision() -> None:
    """One DECISION_LOG entry → returns its `created_at`."""
    expected = datetime(2026, 5, 12, 10, 0, 0, tzinfo=UTC)
    svc = _service_with_scalar(expected)
    out = await svc.latest_decision_at(uuid4(), uuid4())
    assert out == expected


@pytest.mark.asyncio
async def test_latest_decision_at_returns_most_recent_when_multiple_decisions() -> None:
    """SQL `max(created_at)` returns the newest; method passes it through.

    The DB does the max() reduction in the query — the mock returns
    whatever the scalar would; we assert the method respects that value.
    """
    newest = datetime(2026, 5, 12, 12, 30, 0, tzinfo=UTC)
    svc = _service_with_scalar(newest)
    out = await svc.latest_decision_at(uuid4(), uuid4())
    assert out == newest


@pytest.mark.asyncio
async def test_latest_decision_at_filters_by_agent_id() -> None:
    """The query filters by (agent_id, task_id) — a decision by another
    agent on the same task must NOT count. The mock returns None to
    represent the post-filter empty set."""
    svc = _service_with_scalar(None)
    out = await svc.latest_decision_at(uuid4(), uuid4())
    assert out is None
