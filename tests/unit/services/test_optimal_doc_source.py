"""Unit tests for OptimalService doc-source identifier validation.

These guard against the silent ``or 'unknown'`` / ``or None`` fallbacks that
previously masked upstream bugs (e.g. a journal entry being indexed before
its ID was flushed to the database produced ``roboco://journals/None`` rows
in indexed_documents).

Each indexer that builds a ``source`` URI from a caller-supplied identifier
must raise ``ValueError`` instead of stitching a placeholder when that
identifier is missing.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.models.optimal import (
    IndexConversationParams,
    IndexJournalEntryParams,
    IndexReviewParams,
    IndexType,
)
from roboco.services.optimal import OptimalService


class _StubOptimalService(OptimalService):
    """Test-only subclass that bypasses DB tracking.

    The indexer methods we exercise call ``_track_indexed_document`` after
    building the doc-source; we don't want a DB round-trip in unit tests,
    and we want the raise to happen *before* this stub is ever called.
    """

    async def _track_indexed_document(
        self,
        index_type: IndexType,
        source: str,
        title: str | None = None,
        preview: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        # Arguments are deliberately ignored — this stub exists solely to
        # neutralize the DB write in the success-path test.
        del index_type, source, title, preview, metadata


def _service_with_stub_plugin() -> _StubOptimalService:
    """Build a _StubOptimalService with MagicMock plugins.

    The indexer methods short-circuit through ``_get_plugin`` -> plugin
    coroutine; the source-construction step we want to exercise sits
    *after* the plugin call, so any AsyncMock plugin coroutine will do.
    """
    svc = _StubOptimalService()
    plugin = MagicMock()
    plugin.index_entry = AsyncMock()
    plugin.index_message = AsyncMock()
    plugin.record_review = AsyncMock(return_value=MagicMock(doc_id=""))
    plugin.ingest = AsyncMock()
    svc._plugins = {
        IndexType.JOURNALS: plugin,
        IndexType.CONVERSATIONS: plugin,
        IndexType.REVIEWS: plugin,
    }
    svc._initialized = True
    return svc


@pytest.mark.asyncio
async def test_index_journal_entry_raises_when_entry_id_is_none() -> None:
    """``entry_id=None`` must raise — the silent fallback hid an upstream
    bug where the entry row hadn't been flushed before indexing, producing
    ``roboco://journals/None`` doc-sources in the RAG store.

    ``entry_id`` is typed ``UUID`` (required); a real caller would have to
    bypass the dataclass type contract for this to fire (e.g. via
    ``cast(UUID, None)``). We simulate that with a ``SimpleNamespace`` so
    we don't have to reach inside a frozen-style dataclass to clobber a
    field — same pattern used by the conversation test below.
    """
    svc = _service_with_stub_plugin()
    fake_params = SimpleNamespace(
        content="some reflection",
        entry_type="reflect",
        entry_id=None,  # the bug we now reject
        agent_id=uuid4(),
        task_id=uuid4(),
        tags=None,
    )

    with pytest.raises(ValueError, match="entry_id is required"):
        await svc.index_journal_entry(cast("IndexJournalEntryParams", fake_params))


@pytest.mark.asyncio
async def test_index_journal_entry_succeeds_with_real_entry_id() -> None:
    """Sanity check: a flushed entry_id flows through without raising."""
    svc = _service_with_stub_plugin()
    params = IndexJournalEntryParams(
        content="some reflection",
        entry_type="reflect",
        entry_id=uuid4(),
        agent_id=uuid4(),
        task_id=uuid4(),
    )
    await svc.index_journal_entry(params)


@pytest.mark.asyncio
async def test_index_conversation_raises_when_session_id_missing() -> None:
    """``session_id`` is typed UUID (required) but the old code coerced
    falsy values to ``'unknown'``. Reject explicitly so the caller fixes
    the missing flush instead of writing junk doc-sources.

    A real caller would have to bypass the dataclass type contract for
    this to fire (e.g. ``cast(UUID, None)``); we simulate that with a
    ``SimpleNamespace`` so we don't have to reach inside a frozen-style
    dataclass to clobber a field.
    """
    svc = _service_with_stub_plugin()
    fake_params = SimpleNamespace(
        content="hello",
        channel_id=uuid4(),
        session_id=None,  # the runtime bug we now reject
        agent_id=uuid4(),
        task_id=None,
        message_type=None,
    )

    with pytest.raises(ValueError, match="session_id is required"):
        await svc.index_conversation(cast("IndexConversationParams", fake_params))


@pytest.mark.asyncio
async def test_record_review_raises_when_file_path_empty() -> None:
    """``file_path`` is typed ``str`` (required); empty strings produced
    ``roboco://reviews/unknown`` doc-sources. Reject empty-or-missing.
    """
    svc = _service_with_stub_plugin()
    params = IndexReviewParams(
        file_path="",  # empty string -> would hit `or 'unknown'`
        comments=[],
        approved=True,
        summary="ok",
    )

    with pytest.raises(ValueError, match="file_path is required"):
        await svc.record_review(params)
