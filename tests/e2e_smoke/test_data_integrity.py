"""C3 data-integrity smoke: deleting a journal entry de-indexes its RAG chunks.

The full chain: a journal entry is indexed (chunks in ``chunks_journals`` +
an ``indexed_documents`` tracking row), then ``OptimalService.unindex_journal_entry``
runs against a real pgvector store + the real tracking-row repository, and
both the chunks and the tracking row are gone. This proves the C3 fix's
cross-layer wiring end-to-end (no orphaned chunks bleeding into RAG answers).

Deviations from the brief (noted): the e2e app does not mount the journal
route, ``similar_memory`` queries LEARNINGS + PLAYBOOKS (not JOURNALS), and
the JOURNALS plugin's ``initialize()`` requires the ollama embedder (not
available in the smoke env). So this smoke drives the de-index path directly
with a real ``VectorStore`` against the test DB wrapped in a minimal
``OptimalService`` — exercising the exact pgvector + tracking-row deletes
the production path runs. The ``delete_entry`` call site is covered by
``tests/unit/services/test_journal.py``.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from roboco.db.tables import IndexedDocumentTable
from roboco.models.optimal import IndexType
from roboco.services.optimal import OptimalService
from roboco.services.optimal_brain.vector_store import VectorStore
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from tests.e2e_smoke.harness import E2EStack

_SMOKE_DIM = 4  # tiny embedding dim — the table is created fresh per run


def _chunk_dsn(db_url: str) -> str:
    """asyncpg-friendly DSN (postgres:// -> postgresql://)."""
    return db_url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgres://", "postgresql://", 1
    )


@pytest.mark.asyncio
async def test_c3_deleted_journal_unindexed(e2e_stack: E2EStack) -> None:
    entry_id = uuid4()
    source = f"roboco://journals/{entry_id}"
    source_hash = hashlib.sha256(source.encode()).hexdigest()

    # 1. Provision a real VectorStore for the JOURNALS index against the test
    #    DB. ``initialize`` creates ``chunks_journals`` if absent.
    store = VectorStore(
        dsn=_chunk_dsn(e2e_stack.db_url),
        table_name="chunks_journals",
        vector_dimension=_SMOKE_DIM,
        pool_min_size=1,
        pool_max_size=2,
    )
    await store.initialize()
    try:
        # Seed a chunk row for the journal source with a tiny zero vector.
        pool = store._require_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO chunks_journals (content, source, embedding, metadata) "
                "VALUES ($1, $2, $3::vector, $4::jsonb)",
                "private reflection that must not leak post-delete",
                source,
                "[" + ",".join(("0.0",) * _SMOKE_DIM) + "]",
                "{}",
            )

        # 2. Seed the tracking row via SQLAlchemy.
        engine = create_async_engine(e2e_stack.db_url, future=True)
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                row = IndexedDocumentTable(
                    index_type=IndexType.JOURNALS.value,
                    source=source,
                    source_hash=source_hash,
                    title="Journal: reflect",
                    preview="private reflection that must not leak post-delete",
                    chunk_count=1,
                    extra_data={},
                )
                session.add(row)
                await session.commit()

            # 3. Build a minimal OptimalService whose JOURNALS plugin routes
            #    delete_by_source to the real VectorStore. This mirrors how
            #    unindex_playbook's else-branch calls plugin._require_store.
            svc = OptimalService()
            plugin_obj: Any = type("FakeJournalsPlugin", (), {})()
            object.__setattr__(plugin_obj, "_require_store", store)
            svc._plugins = {IndexType.JOURNALS: plugin_obj}
            svc._initialized = True

            # 4. Run the de-index.
            await svc.unindex_journal_entry(entry_id)

            # 5. Assert the chunk row is gone.
            async with pool.acquire() as conn:
                remaining = await conn.fetchval(
                    "SELECT count(*) FROM chunks_journals WHERE source = $1",
                    source,
                )
            assert remaining == 0, "journal chunk rows survived de-index"

            # 6. Assert the tracking row is gone.
            async with factory() as session:
                rows = (
                    await session.execute(
                        select(IndexedDocumentTable).where(
                            IndexedDocumentTable.source_hash == source_hash,
                            IndexedDocumentTable.index_type == IndexType.JOURNALS.value,
                        )
                    )
                ).all()
            assert rows == [], "indexed_documents tracking row survived de-index"
        finally:
            await engine.dispose()
    finally:
        await store.close()
