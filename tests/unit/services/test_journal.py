"""Unit tests for JournalService gateway-backfill methods."""

from __future__ import annotations

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
