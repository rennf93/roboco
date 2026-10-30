"""Documentation-index provenance metadata.

A doc written mid-task via roboco_docs_write (or captured from a workspace
clone at i_documented) describes work that hasn't merged yet — a live
incident traced this to a frontend dev building UI against an API contract
that existed only in a still-open PR, because a roboco_kb_search hit off
such a doc read indistinguishably from one describing merged/deployed
reality. ``prepare_metadata``/``index_sources`` now carry a ``provenance``
marker ("live_write" vs the default "repo_tree") so a reader can tell the
two apart; ``mcp/optimal_server.py`` renders the caveat (separate test file).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.services.optimal_brain.indexes.base import IngestResult
from roboco.services.optimal_brain.indexes.docs import DocsIndexPlugin
from roboco.services.optimal_brain.vector_store import VectorStore

if TYPE_CHECKING:
    from pathlib import Path


def _plugin() -> DocsIndexPlugin:
    return DocsIndexPlugin()


def test_prepare_metadata_defaults_to_repo_tree() -> None:
    """No provenance kwarg supplied (the tree-scan/startup/manual-reindex
    path) -> defaults to repo_tree, with no task_id."""
    meta = _plugin().prepare_metadata("content", file_path="x.md", doc_type="guide")
    assert meta["provenance"] == "repo_tree"
    assert meta["task_id"] is None


def test_prepare_metadata_honors_live_write_override() -> None:
    """roboco_docs_write's index call passes provenance=live_write + the
    writing task's id; both must land verbatim in the chunk metadata."""
    meta = _plugin().prepare_metadata(
        "content",
        file_path="x.md",
        doc_type="api",
        provenance="live_write",
        task_id="11111111-2222-3333-4444-555555555555",
    )
    assert meta["provenance"] == "live_write"
    assert meta["task_id"] == "11111111-2222-3333-4444-555555555555"


@pytest.mark.asyncio
async def test_index_sources_threads_live_write_provenance_into_chunks(
    tmp_path: Path,
) -> None:
    """index_sources(provenance="live_write", task_id=...) must reach
    prepare_metadata for every file in the batch — the real seam
    roboco_docs_write's _index_doc_in_rag drives."""
    doc = tmp_path / "contract.md"
    doc.write_text("# Contract\n\nSome long enough content body.", encoding="utf-8")

    plugin = _plugin()
    captured_kwargs: list[dict] = []

    async def _fake_ingest_batch(documents: list[tuple]) -> list[IngestResult]:
        for _content, _doc_id, kwargs in documents:
            captured_kwargs.append(kwargs)
        return [
            IngestResult(doc_id=doc_id or "x", chunk_count=1, success=True)
            for _content, doc_id, _kwargs in documents
        ]

    mock_ingest = AsyncMock(side_effect=_fake_ingest_batch)
    with patch.object(plugin, "ingest_batch", mock_ingest):
        count, _indexed = await plugin.index_sources(
            [str(doc)], provenance="live_write", task_id="task-123"
        )

    assert count == 1
    assert captured_kwargs
    assert captured_kwargs[0]["provenance"] == "live_write"
    assert captured_kwargs[0]["task_id"] == "task-123"


@pytest.mark.asyncio
async def test_index_sources_defaults_to_repo_tree_provenance(tmp_path: Path) -> None:
    """The tree-scan call path (no provenance kwarg) must still mark chunks
    repo_tree, not silently leave the key out."""
    doc = tmp_path / "map.md"
    doc.write_text("# Map\n\nSome long enough content body.", encoding="utf-8")

    plugin = _plugin()
    captured_kwargs: list[dict] = []

    async def _fake_ingest_batch(documents: list[tuple]) -> list[IngestResult]:
        for _content, _doc_id, kwargs in documents:
            captured_kwargs.append(kwargs)
        return [
            IngestResult(doc_id=doc_id or "x", chunk_count=1, success=True)
            for _content, doc_id, _kwargs in documents
        ]

    mock_ingest = AsyncMock(side_effect=_fake_ingest_batch)
    with patch.object(plugin, "ingest_batch", mock_ingest):
        await plugin.index_sources([str(doc)])

    assert captured_kwargs
    assert captured_kwargs[0]["provenance"] == "repo_tree"
    assert captured_kwargs[0]["task_id"] is None


# ---------------------------------------------------------------------------
# The completion-triggered flip: live_write → repo_tree by stamped task_id
# ---------------------------------------------------------------------------


def _make_store_with_conn() -> tuple[VectorStore, AsyncMock]:
    """VectorStore wired to a mock pool yielding one mock conn (same pattern
    as test_replace_chunks_atomic) so the UPDATE's SQL/params are assertable
    without a live pgvector database."""
    conn = AsyncMock()
    conn.fetch = AsyncMock(return_value=[{"id": 1}, {"id": 2}])
    pool = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)
    store = VectorStore(
        dsn="postgresql://test", table_name="chunks_documentation", vector_dimension=3
    )
    store._pool = pool  # injected for the test
    return store, conn


@pytest.mark.asyncio
async def test_flip_updates_only_matching_live_write_chunks() -> None:
    """The store-level flip UPDATEs chunks whose task_id is one of the
    completing root's subtree ids AND whose current provenance is live_write
    — docs from unrelated tasks (task_id not in the set) and already-
    flipped docs (provenance=repo_tree) are structurally out of scope."""
    store, conn = _make_store_with_conn()

    flipped = await store.flip_provenance(
        ids=["task-a", "task-b"],
        from_value="live_write",
        to_value="repo_tree",
    )

    mock_rows_updated = 2
    assert flipped == mock_rows_updated
    sql, *params = conn.fetch.await_args.args
    assert "UPDATE" in sql and "jsonb_set" in sql and "ANY" in sql
    assert "task_id" in sql and "provenance" in sql
    # One UPDATE with all three bound params; no delete/re-embed/reindex.
    assert conn.fetch.await_count == 1
    assert list(params) == [
        ["task-a", "task-b"],
        "live_write",
        "repo_tree",
    ]


@pytest.mark.asyncio
async def test_flip_with_no_task_ids_is_a_no_op() -> None:
    """An empty subtree id set touches the store never."""
    store, conn = _make_store_with_conn()

    flipped = await store.flip_provenance(
        ids=[], from_value="live_write", to_value="repo_tree"
    )

    assert flipped == 0
    assert conn.fetch.await_count == 0


@pytest.mark.asyncio
async def test_plugin_flip_task_provenance_delegates_to_store() -> None:
    """DocsIndexPlugin.flip_task_provenance routes the subtree ids through
    the narrow metadata-update path — no reindex, no repo-tree re-derivation,
    unrelated tasks never enter the query."""
    plugin = _plugin()
    store = MagicMock()
    mock_rows_updated = 7
    store.flip_provenance = AsyncMock(return_value=mock_rows_updated)
    plugin._store = store
    plugin._initialized = True

    flipped = await plugin.flip_task_provenance(["task-a"])

    assert flipped == mock_rows_updated
    store.flip_provenance.assert_awaited_once_with(
        ids=["task-a"],
        from_value="live_write",
        to_value="repo_tree",
    )


@pytest.mark.asyncio
async def test_plugin_flip_with_empty_task_ids_never_touches_store() -> None:
    plugin = _plugin()
    store = MagicMock()
    store.flip_provenance = AsyncMock()
    plugin._store = store
    plugin._initialized = True

    assert await plugin.flip_task_provenance([]) == 0
    store.flip_provenance.assert_not_awaited()
