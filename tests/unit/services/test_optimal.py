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
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.models.optimal import IndexType
from roboco.services.optimal import OptimalService

if TYPE_CHECKING:
    from pathlib import Path


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


# ---------------------------------------------------------------------------
# Periodic re-scan: deleted-file de-indexing (secondary defect fix)
#
# _check_for_updates detected new/modified auto-index-dir files by mtime but
# never noticed a file removed from disk, so a deleted docs/rag or docs/map
# file kept surfacing in roboco_kb_search forever. _scan_for_deleted_files
# is the pure detection step (diffs self._file_mtimes against a fresh
# rglob); _unindex_deleted_doc_file is the de-index action, mirroring
# unindex_journal_entry's vector-store + tracking-row pattern above.
# ---------------------------------------------------------------------------


def _service_with_stub_docs_plugin() -> OptimalService:
    """Build an OptimalService with a mocked DOCUMENTATION plugin + store,
    mirroring _service_with_stub_journal_plugin above."""
    svc = OptimalService()
    store = MagicMock()
    store.delete_by_source = AsyncMock(return_value=None)
    plugin = MagicMock()
    plugin._require_store = store
    svc._plugins = {IndexType.DOCUMENTATION: plugin}
    svc._initialized = True
    return svc


def test_scan_for_deleted_files_detects_and_prunes(tmp_path: Path) -> None:
    """A file present in _file_mtimes but absent from disk is returned as
    deleted, and dropped from _file_mtimes so it isn't reported again."""
    kept = tmp_path / "kept.md"
    kept.write_text("# kept", encoding="utf-8")
    gone_path = str(tmp_path / "gone.md")  # never written / already removed

    svc = OptimalService()
    svc._file_mtimes = {str(kept): kept.stat().st_mtime, gone_path: 123.0}

    deleted = svc._scan_for_deleted_files(tmp_path)

    assert deleted == [gone_path]
    assert gone_path not in svc._file_mtimes
    assert str(kept) in svc._file_mtimes  # untouched


def test_scan_for_deleted_files_ignores_other_directories(tmp_path: Path) -> None:
    """A tracked path outside the scanned directory (e.g. docs/map's entries
    while scanning docs/rag) must never be reported as deleted."""
    rag_dir = tmp_path / "rag"
    map_dir = tmp_path / "map"
    rag_dir.mkdir()
    map_dir.mkdir()
    other_file = str(map_dir / "CLAUDE.md")  # not under rag_dir at all

    svc = OptimalService()
    svc._file_mtimes = {other_file: 1.0}

    deleted = svc._scan_for_deleted_files(rag_dir)

    assert deleted == []
    assert other_file in svc._file_mtimes


@pytest.mark.asyncio
async def test_unindex_deleted_doc_file_removes_chunks_and_tracking_row() -> None:
    """De-index calls the vector store with the roboco://docs/ source URI and
    drops the IndexedDocumentTable row keyed on the bare path — matching how
    DocsIndexPlugin.build_source_uri and _build_indexed_files independently
    format the same file's identity at write time."""
    file_path = "/app/docs/rag/tools/kb-tools.md"
    svc = _service_with_stub_docs_plugin()
    plugin: Any = svc._plugins[IndexType.DOCUMENTATION]
    session = _fake_session()

    with patch("roboco.db.get_db_context", lambda: _fake_db_context(session)):
        await svc._unindex_deleted_doc_file(file_path, "rag")

    plugin._require_store.delete_by_source.assert_awaited_once_with(
        f"roboco://docs/{file_path}"
    )
    session.execute.assert_awaited_once()
    stmt = session.execute.await_args.args[0]
    sql_text = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "indexed_documents" in sql_text
    expected_hash = hashlib.sha256(file_path.encode()).hexdigest()
    assert expected_hash in sql_text
    assert IndexType.DOCUMENTATION.value in sql_text


@pytest.mark.asyncio
async def test_unindex_deleted_doc_file_skips_standards_subdir() -> None:
    """A deleted docs/rag/standards/*.md file is skipped (ponytail ceiling —
    standards are parsed into multiple per-rule chunks with hashed-title
    source URIs, not reversible from the file path alone). No store/DB call
    is made."""
    svc = _service_with_stub_docs_plugin()
    plugin: Any = svc._plugins[IndexType.DOCUMENTATION]

    await svc._unindex_deleted_doc_file("/app/docs/rag/standards/python.md", "rag")

    plugin._require_store.delete_by_source.assert_not_awaited()


@pytest.mark.asyncio
async def test_unindex_deleted_doc_file_swallows_vector_store_failure() -> None:
    """A vector-store failure is logged + swallowed; the tracking-row delete
    is skipped since the chunks may still be present. No exception escapes."""
    svc = _service_with_stub_docs_plugin()
    plugin: Any = svc._plugins[IndexType.DOCUMENTATION]
    plugin._require_store.delete_by_source = AsyncMock(
        side_effect=RuntimeError("pgvector blew up")
    )
    session = _fake_session()

    with patch("roboco.db.get_db_context", lambda: _fake_db_context(session)):
        await svc._unindex_deleted_doc_file("/app/docs/rag/x.md", "rag")

    session.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_for_updates_deindexes_a_deleted_file(tmp_path: Path) -> None:
    """End-to-end: a file indexed on a prior scan, then removed from disk,
    is de-indexed on the next _check_for_updates pass."""
    docs_root = tmp_path / "docs"
    rag_dir = docs_root / "rag"
    rag_dir.mkdir(parents=True)
    (docs_root / "map").mkdir()
    gone = rag_dir / "gone.md"
    gone_path = str(gone)
    # Simulate: this file was indexed on a prior scan and has since been
    # deleted from disk (never written in this test).

    svc = object.__new__(OptimalService)
    svc._docs_root = docs_root
    svc._file_mtimes = {gone_path: 111.0}

    unindex_mock = AsyncMock()
    with patch.object(svc, "_unindex_deleted_doc_file", new=unindex_mock):
        await svc._check_for_updates()

    unindex_mock.assert_awaited_once_with(gone_path, "rag")
    assert gone_path not in svc._file_mtimes


@pytest.mark.asyncio
async def test_check_for_updates_does_not_deindex_existing_files(
    tmp_path: Path,
) -> None:
    """A file that still exists on disk (new or unchanged) must never be
    passed to the de-index helper."""
    docs_root = tmp_path / "docs"
    rag_dir = docs_root / "rag"
    rag_dir.mkdir(parents=True)
    (docs_root / "map").mkdir()
    (rag_dir / "still-here.md").write_text("# still here", encoding="utf-8")

    svc = object.__new__(OptimalService)
    svc._docs_root = docs_root
    svc._file_mtimes = {}

    unindex_mock = AsyncMock()
    index_mock = AsyncMock()
    with (
        patch.object(svc, "_unindex_deleted_doc_file", new=unindex_mock),
        patch.object(svc, "_index_doc_file", new=index_mock),
    ):
        await svc._check_for_updates()

    unindex_mock.assert_not_awaited()


# ---------------------------------------------------------------------------
# Restart blind spot: _file_mtimes is in-memory, so a file deleted while the
# process was down (or in a prior process) is invisible to the sweep above —
# it was never re-tracked, so it's never reported "missing". This DB-seeded
# reconcile runs once at startup and catches exactly that gap by diffing
# tracked IndexedDocumentTable rows against the live filesystem.
# ---------------------------------------------------------------------------


def _fake_doc_row(source: str) -> Any:
    row = MagicMock()
    row.source = source
    return row


@pytest.mark.asyncio
async def test_reconcile_deleted_docs_from_db_deindexes_missing_row(
    tmp_path: Path,
) -> None:
    """A tracked row whose file no longer exists on disk is de-indexed, even
    though this process never saw it (empty _file_mtimes)."""
    docs_root = tmp_path / "docs"
    (docs_root / "rag").mkdir(parents=True)
    (docs_root / "map").mkdir()
    gone_path = str(docs_root / "rag" / "gone.md")  # never written

    svc = object.__new__(OptimalService)
    svc._docs_root = docs_root

    unindex_mock = AsyncMock()
    repo_mock = AsyncMock(return_value=[_fake_doc_row(gone_path)])
    with (
        patch.object(svc, "_unindex_deleted_doc_file", new=unindex_mock),
        patch(
            "roboco.services.repositories.IndexedDocumentRepository.get_by_index_type",
            repo_mock,
        ),
        patch("roboco.db.get_db_context", lambda: _fake_db_context(MagicMock())),
    ):
        await svc._reconcile_deleted_docs_from_db()

    unindex_mock.assert_awaited_once_with(gone_path, "rag")


@pytest.mark.asyncio
async def test_reconcile_deleted_docs_from_db_skips_present_and_out_of_scope(
    tmp_path: Path,
) -> None:
    """A row whose file still exists, and a row outside any auto-index dir
    (e.g. a live_write doc written into a dev's workspace clone), are both
    left alone."""
    docs_root = tmp_path / "docs"
    (docs_root / "rag").mkdir(parents=True)
    (docs_root / "map").mkdir()
    still_here = docs_root / "rag" / "still-here.md"
    still_here.write_text("# still here", encoding="utf-8")
    elsewhere = str(tmp_path / "workspaces" / "be-dev-1" / "docs" / "x.md")

    svc = object.__new__(OptimalService)
    svc._docs_root = docs_root

    unindex_mock = AsyncMock()
    repo_mock = AsyncMock(
        return_value=[_fake_doc_row(str(still_here)), _fake_doc_row(elsewhere)]
    )
    with (
        patch.object(svc, "_unindex_deleted_doc_file", new=unindex_mock),
        patch(
            "roboco.services.repositories.IndexedDocumentRepository.get_by_index_type",
            repo_mock,
        ),
        patch("roboco.db.get_db_context", lambda: _fake_db_context(MagicMock())),
    ):
        await svc._reconcile_deleted_docs_from_db()

    unindex_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_reconcile_deleted_docs_from_db_swallows_query_failure(
    tmp_path: Path,
) -> None:
    """A DB error during the reconcile query is logged and swallowed — this
    is a best-effort catch-up, never a startup blocker."""
    docs_root = tmp_path / "docs"
    (docs_root / "rag").mkdir(parents=True)

    svc = object.__new__(OptimalService)
    svc._docs_root = docs_root

    repo_mock = AsyncMock(side_effect=RuntimeError("db unavailable"))
    with (
        patch(
            "roboco.services.repositories.IndexedDocumentRepository.get_by_index_type",
            repo_mock,
        ),
        patch("roboco.db.get_db_context", lambda: _fake_db_context(MagicMock())),
    ):
        # Must not raise.
        await svc._reconcile_deleted_docs_from_db()
