"""VectorStore.initialize() must provision an HNSW index on the embedding
column, matching the `<=>` cosine operator `search`/`hybrid_search` order by.
Production's chunks_journals had 196 seq scans / 6.1M tuples read against
only 387 live rows -- the ivfflat index alone (trained on whatever rows
existed at CREATE TIME, i.e. none, on a fresh table) never gave the planner
anything to trust.

Uses a dedicated table name rather than the real `chunks_journals` --
`test_rag_backfill.py` creates its own columnless `chunks_journals` against
this same shared test DB, and a full VectorStore-provisioned schema there
(content NOT NULL) breaks that suite's minimal-shape inserts. The index DDL
this exercises is table-name-agnostic; VectorStore issues the identical
statement for `chunks_journals` in production.
"""

from __future__ import annotations

import pytest
from roboco.services.optimal_brain.vector_store import VectorStore


def _chunk_dsn(db_url: str) -> str:
    """asyncpg-friendly DSN (postgresql+asyncpg:// -> postgresql://)."""
    return db_url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgres://", "postgresql://", 1
    )


@pytest.mark.asyncio
async def test_initialize_creates_hnsw_index(_test_database_url: str) -> None:
    table = "chunks_hnsw_probe"
    store = VectorStore(
        dsn=_chunk_dsn(_test_database_url),
        table_name=table,
        vector_dimension=8,
        pool_min_size=1,
        pool_max_size=2,
    )
    await store.initialize()
    try:
        pool = store._require_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT indexdef FROM pg_indexes "
                "WHERE tablename = $1 AND indexname = $2",
                table,
                f"{table}_embedding_hnsw_idx",
            )
    finally:
        await store.close()

    assert len(rows) == 1
    indexdef = rows[0]["indexdef"]
    assert "hnsw" in indexdef
    assert "vector_cosine_ops" in indexdef


@pytest.mark.asyncio
async def test_initialize_drops_the_old_ivfflat_index(_test_database_url: str) -> None:
    """The ivfflat index this table used to also carry is now redundant

    (see the module docstring) and must not be recreated on every deploy.
    """
    table = "chunks_hnsw_probe_no_ivfflat"
    store = VectorStore(
        dsn=_chunk_dsn(_test_database_url),
        table_name=table,
        vector_dimension=8,
        pool_min_size=1,
        pool_max_size=2,
    )
    await store.initialize()
    try:
        pool = store._require_pool()
        async with pool.acquire() as conn:
            names = {
                r["indexname"]
                for r in await conn.fetch(
                    "SELECT indexname FROM pg_indexes WHERE tablename = $1", table
                )
            }
    finally:
        await store.close()

    assert f"{table}_embedding_hnsw_idx" in names
    assert f"{table}_embedding_idx" not in names


@pytest.mark.asyncio
async def test_initialize_repairs_an_invalid_hnsw_index(
    _test_database_url: str,
) -> None:
    """A CONCURRENTLY build that failed partway leaves indisvalid=false;

    Postgres never retries it on its own, so a later `initialize()` (e.g. on
    process restart) must drop and rebuild it instead of leaving it dead.
    """
    table = "chunks_hnsw_probe_repair"
    dsn = _chunk_dsn(_test_database_url)
    index_name = f"{table}_embedding_hnsw_idx"

    store = VectorStore(
        dsn=dsn, table_name=table, vector_dimension=8, pool_min_size=1, pool_max_size=2
    )
    await store.initialize()
    try:
        pool = store._require_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE pg_index SET indisvalid = false "
                "WHERE indexrelid = to_regclass($1)",
                index_name,
            )
    finally:
        await store.close()

    # A fresh VectorStore against the same table simulates a process restart
    # (initialize() no-ops on an already-open pool, so re-using `store` here
    # would skip the repair path entirely).
    store2 = VectorStore(
        dsn=dsn, table_name=table, vector_dimension=8, pool_min_size=1, pool_max_size=2
    )
    await store2.initialize()
    try:
        pool = store2._require_pool()
        async with pool.acquire() as conn:
            valid = await conn.fetchval(
                "SELECT indisvalid FROM pg_index WHERE indexrelid = to_regclass($1)",
                index_name,
            )
    finally:
        await store2.close()

    assert valid is True
