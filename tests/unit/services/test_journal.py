"""Unit tests for JournalService gateway-backfill methods."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

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


@pytest.mark.asyncio
async def test_write_decision_calls_add_decision_log_with_task_id() -> None:
    """write_decision delegates to add_decision_log with a DECISION_LOG entry.

    Title comes from the first content line (truncated to 100 chars); the
    full content is carried in the rationale field so the auto-recorded
    decision reads coherently in journal lists and RAG retrieval.
    """
    svc = JournalService(MagicMock(flush=AsyncMock()))
    add_decision_mock = AsyncMock(
        return_value=MagicMock(type=JournalEntryType.DECISION_LOG)
    )
    _bind(svc, "add_decision_log", add_decision_mock)
    agent_id = uuid4()
    task_id = uuid4()
    content = "Completing PR #120: all 3 acceptance criteria verified\nmore detail"
    out = await svc.write_decision(agent_id=agent_id, task_id=task_id, content=content)
    assert out is not None
    add_decision_mock.assert_awaited_once()
    args, _kwargs = add_decision_mock.call_args
    assert args[0] == agent_id
    params = args[1]
    assert params.task_id == task_id
    assert params.title == "Completing PR #120: all 3 acceptance criteria verified"
    assert content in params.rationale


@pytest.mark.asyncio
async def test_write_decision_handles_empty_content_gracefully() -> None:
    svc = JournalService(MagicMock(flush=AsyncMock()))
    add_decision_mock = AsyncMock(return_value=MagicMock())
    _bind(svc, "add_decision_log", add_decision_mock)
    await svc.write_decision(agent_id=uuid4(), task_id=uuid4(), content="")
    args, _kwargs = add_decision_mock.call_args
    params = args[1]
    assert params.title == "Decision"


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


# ---------------------------------------------------------------------------
# delete_entry — C3: must de-index the entry from the RAG after the row
# commit so deleted/private journal content stops bleeding into claim-time
# briefings. Best-effort: a de-index failure never errors the delete.
# ---------------------------------------------------------------------------


def _entry(journal_id: UUID, entry_id: UUID) -> Any:
    entry = MagicMock()
    entry.id = entry_id
    entry.journal_id = journal_id
    entry.type = JournalEntryType.GENERAL
    return entry


def _journal(journal_id: UUID) -> Any:
    journal = MagicMock()
    journal.id = journal_id
    journal.total_entries = 1
    journal.entries_by_type = {JournalEntryType.GENERAL.value: 1}
    return journal


def _session_for_delete(entry: Any, journal: Any) -> Any:
    """Session whose two queries return the entry then its journal, and
    whose delete/commit are AsyncMocks."""
    session = MagicMock()
    result_entry = MagicMock()
    result_entry.scalar_one_or_none.return_value = entry
    result_journal = MagicMock()
    result_journal.scalar_one_or_none.return_value = journal
    session.execute = AsyncMock(side_effect=[result_entry, result_journal])
    session.delete = AsyncMock()
    session.commit = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_delete_entry_deindexes_after_commit() -> None:
    """C3: delete_entry commits the row delete, then calls
    unindex_journal_entry with the entry id so the RAG chunks + tracking
    row are removed. The OptimalService singleton is patched so no real
    pgvector round-trip happens."""
    journal_id = uuid4()
    entry_id = uuid4()
    entry = _entry(journal_id, entry_id)
    journal = _journal(journal_id)
    session = _session_for_delete(entry, journal)
    svc = JournalService(session)

    optimal = MagicMock()
    optimal.unindex_journal_entry = AsyncMock(return_value=None)
    with (
        patch(
            "roboco.services.optimal.get_optimal_service",
            AsyncMock(return_value=optimal),
        ),
    ):
        out = await svc.delete_entry(entry_id)

    assert out is True
    session.commit.assert_awaited_once()
    optimal.unindex_journal_entry.assert_awaited_once_with(entry_id)


@pytest.mark.asyncio
async def test_delete_entry_swallows_deindex_failure() -> None:
    """A de-index failure must not error the delete — the row is already
    deleted, so the caller's contract (returns True) holds."""
    journal_id = uuid4()
    entry_id = uuid4()
    entry = _entry(journal_id, entry_id)
    journal = _journal(journal_id)
    session = _session_for_delete(entry, journal)
    svc = JournalService(session)

    optimal = MagicMock()
    optimal.unindex_journal_entry = AsyncMock(side_effect=RuntimeError("rag blew up"))
    with (
        patch(
            "roboco.services.optimal.get_optimal_service",
            AsyncMock(return_value=optimal),
        ),
    ):
        out = await svc.delete_entry(entry_id)

    assert out is True
    session.commit.assert_awaited_once()
