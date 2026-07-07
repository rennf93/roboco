"""Unit tests for OptimalService de-index helpers.

C3: ``unindex_journal_entry`` must remove a journal entry's embedded chunks
from the vector store AND its ``indexed_documents`` tracking row, so a
deleted (or private) entry stops surfacing in RAG answers and claim-time
briefings. Idempotent — a never-indexed entry is a clean no-op. Best-effort
— a failure never raises.

Mirrors the mock-based pattern in ``test_optimal_doc_source.py`` and
``test_playbook_unindex.py``: the JOURNALS plugin and ``get_db_context`` are
stubbed so the test exercises only the de-index wiring (no real pgvector
round-trip in unit tests).
"""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.models.optimal import IndexType
from roboco.services.optimal import OptimalService


def _service_with_stub_journal_plugin() -> OptimalService:
    """Build an OptimalService with a mocked JOURNALS plugin + store.

    The plugin's ``_require_store.delete_by_source`` is an AsyncMock the test
    asserts on; it stands in for the vector-store chunk removal.
    """
    svc = OptimalService()
    store = MagicMock()
    store.delete_by_source = AsyncMock(return_value=None)
    plugin = MagicMock()
    plugin._require_store = store
    svc._plugins = {IndexType.JOURNALS: plugin}
    svc._initialized = True
    return svc


def _fake_session() -> Any:
    """Async session mock whose execute returns a result with rowcount=1."""
    session = MagicMock()
    result = MagicMock()
    result.rowcount = 1
    session.execute = AsyncMock(return_value=result)
    session.flush = AsyncMock()
    return session


@asynccontextmanager
async def _fake_db_context(session: Any) -> Any:
    yield session


@pytest.mark.asyncio
async def test_unindex_journal_entry_removes_chunks_and_tracking_row() -> None:
    """De-index calls the vector-store delete_by_source with the journal
    source URI, then drops the tracking row via the repository. Both calls
    receive ``roboco://journals/{entry_id}`` and the JOURNALS index type."""
    entry_id = uuid4()
    svc = _service_with_stub_journal_plugin()
    plugin: Any = svc._plugins[IndexType.JOURNALS]
    session = _fake_session()

    with patch(
        "roboco.db.get_db_context",
        lambda: _fake_db_context(session),
    ):
        await svc.unindex_journal_entry(entry_id)

    expected_source = f"roboco://journals/{entry_id}"
    plugin._require_store.delete_by_source.assert_awaited_once_with(expected_source)
    # The tracking-row delete ran against the JOURNALS index + journal source.
    session.execute.assert_awaited_once()
    session.flush.assert_awaited_once()
    # The DELETE statement filters indexed_documents by index_type + source_hash;
    # the repo computes the hash from the source URI (sha256), so assert the
    # bind param matches the journal source's hash.
    call = session.execute.await_args
    stmt = call.args[0]
    compiled = stmt.compile(compile_kwargs={"literal_binds": True})
    sql_text = str(compiled)
    assert "indexed_documents" in sql_text
    expected_hash = hashlib.sha256(expected_source.encode()).hexdigest()
    assert expected_hash in sql_text
    assert IndexType.JOURNALS.value in sql_text


@pytest.mark.asyncio
async def test_unindex_journal_entry_is_idempotent() -> None:
    """A second call is a clean no-op: the vector store + repo deletes are
    called again (idempotent on a missing source) and no exception raised."""
    entry_id = uuid4()
    svc = _service_with_stub_journal_plugin()
    session = _fake_session()

    with patch(
        "roboco.db.get_db_context",
        lambda: _fake_db_context(session),
    ):
        await svc.unindex_journal_entry(entry_id)
        # Second call must not raise even though the source is already gone.
        await svc.unindex_journal_entry(entry_id)

    expected_calls = 2  # one per de-index invocation
    journal_plugin: Any = svc._plugins[IndexType.JOURNALS]
    assert journal_plugin._require_store.delete_by_source.await_count == expected_calls
    assert session.execute.await_count == expected_calls


@pytest.mark.asyncio
async def test_unindex_journal_entry_swallows_vector_store_failure() -> None:
    """A vector-store failure is logged + swallowed and the tracking-row
    delete is skipped (the chunks may still be present, so dropping the
    tracking row would lie about what's indexed). No exception escapes."""
    entry_id = uuid4()
    svc = _service_with_stub_journal_plugin()
    plugin: Any = svc._plugins[IndexType.JOURNALS]
    plugin._require_store.delete_by_source = AsyncMock(
        side_effect=RuntimeError("pgvector blew up")
    )
    session = _fake_session()

    with patch(
        "roboco.db.get_db_context",
        lambda: _fake_db_context(session),
    ):
        # Must not raise.
        await svc.unindex_journal_entry(entry_id)

    # Tracking row NOT dropped when chunks remain.
    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_unindex_journal_entry_swallows_tracking_row_failure() -> None:
    """A tracking-row delete failure is logged + swallowed; the chunks were
    already removed so the orphaned tracking row is a stale cosmetic, not a
    leak. No exception escapes."""
    entry_id = uuid4()
    svc = _service_with_stub_journal_plugin()
    plugin: Any = svc._plugins[IndexType.JOURNALS]
    plugin._require_store.delete_by_source = AsyncMock(return_value=None)

    # Session whose execute raises — the real repo propagates this.
    session = MagicMock()
    session.execute = AsyncMock(side_effect=RuntimeError("db connection lost"))
    session.flush = AsyncMock()

    with patch("roboco.db.get_db_context", lambda: _fake_db_context(session)):
        # Must not raise.
        await svc.unindex_journal_entry(entry_id)

    plugin._require_store.delete_by_source.assert_awaited_once()
