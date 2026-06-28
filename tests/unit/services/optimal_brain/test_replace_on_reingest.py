"""Re-ingesting a source must replace its chunks, not append duplicates.

Guards the HIGH fix: without a delete-by-source step, every startup/periodic/
manual reindex appended a fresh copy of each doc's chunks, growing the tables
unbounded and crowding out distinct results. The carve-out is conversations,
whose many messages share one source URI — there, append must be preserved.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.services.optimal_brain.indexes.conversations import ConversationsIndexPlugin
from roboco.services.optimal_brain.indexes.standards import StandardsIndexPlugin
from roboco.services.optimal_brain.text_chunker import Chunk, Document


def _wire_plugin(
    plugin: object, source: str, monkeypatch: pytest.MonkeyPatch
) -> AsyncMock:
    """Attach mock store/chunker/embedder via monkeypatch; return the store mock."""
    chunk = Chunk(text="x" * 250, source=source, metadata={})
    store = AsyncMock()
    embedder = MagicMock()
    embedder.aembed_chunks = AsyncMock(return_value=[chunk])
    monkeypatch.setattr(plugin, "_store", store)
    monkeypatch.setattr(
        plugin, "_chunker", MagicMock(chunk_document=MagicMock(return_value=[chunk]))
    )
    monkeypatch.setattr(plugin, "_embedder", embedder)
    monkeypatch.setattr(plugin, "_initialized", True)
    return store


def test_default_plugin_replaces_on_reingest() -> None:
    assert StandardsIndexPlugin.replace_on_reingest is True


def test_conversations_plugin_appends_not_replaces() -> None:
    assert ConversationsIndexPlugin.replace_on_reingest is False


@pytest.mark.asyncio
async def test_reingest_replaces_source_chunks_atomically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Re-ingest must replace the source's chunks in ONE atomic call (F108).

    The replace path used to be two separate awaits — ``delete_by_source``
    then ``add_chunks`` — each acquiring its own pool connection. Two
    concurrent re-indexes of the same source interleaved (A.delete, B.delete,
    A.add, B.add) and produced duplicate chunk rows. The fix collapses the
    delete + insert into a single transactional ``replace_chunks`` call so
    the whole replace is atomic (concurrent replacers serialize; the last
    committer wins with no duplicates).
    """
    plugin = StandardsIndexPlugin()
    source = "roboco://standards/general/std-1"
    store = _wire_plugin(plugin, source, monkeypatch)
    store.replace_chunks = AsyncMock()
    doc = Document(content="x" * 250, source=source, metadata={})

    count = await plugin._chunk_filter_embed_store(doc, {})

    assert count == 1
    store.replace_chunks.assert_awaited_once()
    # The atomic path must NOT also issue the separate delete/add calls —
    # that would re-open the non-atomic race the single call closes.
    store.delete_by_source.assert_not_awaited()
    store.add_chunks.assert_not_awaited()
    # The source and the embedded chunks are passed through verbatim.
    called_source, called_chunks = store.replace_chunks.await_args.args
    assert called_source == source
    assert len(called_chunks) == 1


@pytest.mark.asyncio
async def test_reingest_preserves_history_for_conversations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = ConversationsIndexPlugin()
    source = "roboco://conversations/sess-1-agent-1"
    store = _wire_plugin(plugin, source, monkeypatch)
    doc = Document(content="x" * 250, source=source, metadata={})

    count = await plugin._chunk_filter_embed_store(doc, {})

    assert count == 1
    store.delete_by_source.assert_not_awaited()
    store.add_chunks.assert_awaited_once()
