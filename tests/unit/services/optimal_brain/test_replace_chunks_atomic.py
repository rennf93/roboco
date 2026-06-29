"""``VectorStore.replace_chunks`` is a single atomic transaction: delete +
insert on ONE connection inside ONE asyncpg transaction, so concurrent
re-indexes can't interleave and an insert failure can't lose the source's
rows. These tests mock the asyncpg pool to assert that invariant.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.services.optimal_brain.text_chunker import Chunk
from roboco.services.optimal_brain.vector_store import VectorStore


def _chunk(text: str, source: str, embedding: list[float]) -> Chunk:
    return Chunk(text=text, source=source, metadata={}, embedding=embedding)


def _make_store_with_conn() -> tuple[VectorStore, MagicMock, MagicMock]:
    """Build a VectorStore wired to a mock pool that yields one mock conn.

    Returns ``(store, conn, pool)`` so a test can assert call order on the
    same connection instance that the transaction/DELETE/INSERT ran on.
    """
    conn = MagicMock()
    # asyncpg's transaction() is an async context manager.
    tx = MagicMock()
    tx.__aenter__ = AsyncMock(return_value=tx)
    tx.__aexit__ = AsyncMock(return_value=None)
    conn.transaction = MagicMock(return_value=tx)
    conn.execute = AsyncMock()
    conn.executemany = AsyncMock()

    pool = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=conn)
    cm.__aexit__ = AsyncMock(return_value=None)
    pool.acquire = MagicMock(return_value=cm)

    store = VectorStore(
        dsn="postgresql://test", table_name="chunks_test", vector_dimension=3
    )
    store._pool = pool  # injected for the test
    return store, conn, pool


@pytest.mark.asyncio
async def test_replace_chunks_deletes_then_inserts_in_one_transaction() -> None:
    """DELETE + INSERT run on a SINGLE connection inside a transaction."""
    store, conn, pool = _make_store_with_conn()
    source = "roboco://standards/general/std-1"
    chunks = [
        _chunk("aaa" * 100, source, [0.1, 0.2, 0.3]),
        _chunk("bbb" * 100, source, [0.4, 0.5, 0.6]),
    ]

    await store.replace_chunks(source, chunks)

    # Exactly one connection was acquired (not one for delete + one for add).
    assert pool.acquire.call_count == 1
    # The transaction was entered on that same connection.
    assert conn.transaction.call_count == 1
    # DELETE by source, then the batch INSERT, both on the same connection.
    assert conn.execute.await_count == 1
    delete_sql, delete_source = conn.execute.await_args.args
    assert "DELETE FROM" in delete_sql
    assert "source = $1" in delete_sql
    assert delete_source == source
    assert conn.executemany.await_count == 1
    insert_sql, insert_records = conn.executemany.await_args.args
    assert "INSERT INTO" in insert_sql
    assert len(insert_records) == len(chunks)


@pytest.mark.asyncio
async def test_replace_chunks_with_no_chunks_still_clears_source() -> None:
    """An empty reingest still deletes the source's existing rows (matches
    the prior ``delete_by_source`` then no-op ``add_chunks`` behavior)."""
    store, conn, _pool = _make_store_with_conn()
    source = "roboco://standards/general/std-1"

    await store.replace_chunks(source, [])

    assert conn.execute.await_count == 1  # DELETE ran
    assert conn.executemany.await_count == 0  # nothing to insert


@pytest.mark.asyncio
async def test_replace_chunks_atomic_on_insert_failure() -> None:
    """If the INSERT fails, the transaction rolls back — the source's old
    rows are NOT left deleted (no retrieval data loss). The DELETE and
    INSERT share one transaction, so a mid-replace failure reverts both."""
    store, conn, _pool = _make_store_with_conn()
    source = "roboco://standards/general/std-1"
    chunks = [_chunk("aaa" * 100, source, [0.1, 0.2, 0.3])]
    # The INSERT raises inside the transaction.
    conn.executemany.side_effect = RuntimeError("insert blew up")

    with pytest.raises(RuntimeError):
        await store.replace_chunks(source, chunks)

    # The transaction context was entered; the raised error propagates out of
    # the `async with conn.transaction()` block, so asyncpg rolls it back.
    assert conn.transaction.call_count == 1
    assert conn.execute.await_count == 1  # DELETE did run (inside the txn)…
    assert conn.executemany.await_count == 1  # …but the INSERT raised → rollback


@pytest.mark.asyncio
async def test_replace_chunks_skips_chunks_without_embeddings() -> None:
    """Chunks lacking an embedding are dropped before insert (matches
    ``add_chunks``)."""
    store, conn, _pool = _make_store_with_conn()
    source = "roboco://standards/general/std-1"
    chunks = [
        _chunk("with-emb", source, [0.1, 0.2, 0.3]),
        Chunk(text="no-emb", source=source, metadata={}),  # no embedding
    ]

    await store.replace_chunks(source, chunks)

    assert conn.execute.await_count == 1  # DELETE ran
    _sql, records = conn.executemany.await_args.args
    assert len(records) == 1  # only the embedded chunk inserted


@pytest.mark.asyncio
async def test_replace_chunks_does_not_wipe_on_all_none_embeddings() -> None:
    """#181: chunks were passed (an embedder ran) but NONE produced a usable
    embedding — that is an embedder failure, not a deliberate clear. Wiping the
    source's existing rows on a failed embed would lose good index rows for
    nothing. The replace must no-op (no DELETE) in that case, distinct from the
    deliberate empty-list clear (which DOES delete)."""
    store, conn, _pool = _make_store_with_conn()
    source = "roboco://learnings/lrn-deadbeef"

    chunks = [
        Chunk(text="failed-embed-1", source=source, metadata={}),  # no embedding
        Chunk(text="failed-embed-2", source=source, metadata={}),  # no embedding
    ]
    await store.replace_chunks(source, chunks)

    # No-op: no connection acquired, no DELETE, no INSERT — existing rows kept.
    assert conn.execute.await_count == 0
    assert conn.executemany.await_count == 0
