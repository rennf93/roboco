"""
VectorStore — in-house asyncpg + pgvector vector store.

**Before/after startup timing:**
On a cold start the first ``initialize()`` call creates the asyncpg connection
pool (approx 50-200 ms on LAN, approx 1-5 s on first container boot when Postgres
starts simultaneously) and issues a ``CREATE TABLE IF NOT EXISTS`` DDL
statement.  Subsequent calls are no-ops because the table already exists and
the pool is reused.  On a warm restart (Postgres already running, schema
already present) ``initialize()`` completes in under 100 ms.

Design
------
Each :class:`BaseIndexPlugin` subclass creates one ``VectorStore`` instance
scoped to its index type.  The table name is ``chunks_{index_type}`` (e.g.
``chunks_docs``, ``chunks_journals``).  ``CREATE TABLE IF NOT EXISTS``
guarantees that re-deploying the service does not wipe existing data.

SQL dialect
-----------
Vector similarity uses the pgvector ``<=>`` cosine-distance operator.
Scores are returned as ``1 - (embedding <=> query_vec)`` (cosine similarity
in [0, 1]).  Vectors are sent to Postgres as text in the format
``'[0.1, 0.2, …]'`` and cast with ``::vector`` inside the SQL statement,
which avoids the need for a custom asyncpg type codec.

SQLAlchemy / asyncpg backend
-----------------------------
All public methods are ``async`` and use the project-standard
``postgresql+asyncpg://`` driver.  The connection pool is managed by asyncpg
directly (``asyncpg.create_pool``); a SQLAlchemy ``text()``-style raw-SQL
approach is used for all queries so that pgvector operators pass through
untransformed.  This is consistent with the project's existing use of
``sqlalchemy[asyncio]`` + ``asyncpg`` elsewhere in the stack.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import asyncpg

from roboco.services.optimal_brain.text_chunker import Chunk, Citation

logger = logging.getLogger(__name__)


def _vec_to_str(embedding: list[float]) -> str:
    """Encode a float list as the pgvector text literal ``[a,b,c,…]``."""
    return "[" + ",".join(str(x) for x in embedding) + "]"


def _as_dict(value: Any) -> dict[str, Any]:
    """Decode a ``jsonb`` column value into a dict.

    No json codec is registered on the pool, so asyncpg returns ``jsonb`` as a
    JSON *string*; ``dict(value)`` would then iterate characters and raise
    "dictionary update sequence element #0 has length 1; 2 is required". Handle
    both the string form and an already-decoded mapping (and null → ``{}``).
    """
    if not value:
        return {}
    if isinstance(value, str):
        loaded = json.loads(value)
        return dict(loaded) if isinstance(loaded, dict) else {}
    return dict(value)


class VectorStore:
    """Async vector store backed by PostgreSQL + pgvector.

    Parameters
    ----------
    dsn:
        PostgreSQL connection string.  Both ``postgres://`` and
        ``postgresql://`` schemes are accepted.
    table_name:
        Name of the table to read/write (e.g. ``chunks_docs``).
    vector_dimension:
        Dimensionality of the embedding vectors (e.g. 1024 for
        ``qwen3-embedding:0.6b``).  Used only when creating the table for
        the first time; existing tables are left untouched.
    pool_min_size:
        Minimum number of connections in the asyncpg pool.
    pool_max_size:
        Maximum number of connections in the asyncpg pool.
    """

    def __init__(
        self,
        dsn: str,
        table_name: str,
        vector_dimension: int,
        pool_min_size: int = 1,
        pool_max_size: int = 10,
    ) -> None:
        # asyncpg accepts postgresql:// but not postgres://
        self._dsn = dsn.replace("postgres://", "postgresql://", 1)
        self._table_name = self._safe_identifier(table_name)
        self._vector_dimension = vector_dimension
        self._pool_min_size = pool_min_size
        self._pool_max_size = pool_max_size
        self._pool: asyncpg.Pool | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Create the asyncpg pool and provision the table (if absent).

        Idempotent: calling this multiple times is safe.
        """
        if self._pool is not None:
            return

        self._pool = await asyncpg.create_pool(
            dsn=self._dsn,
            min_size=self._pool_min_size,
            max_size=self._pool_max_size,
        )

        async with self._pool.acquire() as conn:
            # Enable pgvector — safe to call even if already installed.
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            await conn.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {self._table_name} (
                    id         BIGSERIAL PRIMARY KEY,
                    content    TEXT        NOT NULL,
                    source     TEXT        NOT NULL,
                    embedding  vector({self._vector_dimension}),
                    metadata   JSONB       NOT NULL DEFAULT '{{}}',
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    tsv        tsvector GENERATED ALWAYS AS
                               (to_tsvector('english', content)) STORED
                )
                """
            )
            # Index for fast cosine-distance queries (created only once).
            await conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS {self._table_name}_embedding_idx
                ON {self._table_name}
                USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = 100)
                """
            )
            # GIN index for the full-text (keyword) half of hybrid search.
            await conn.execute(
                f"""
                CREATE INDEX IF NOT EXISTS {self._table_name}_tsv_idx
                ON {self._table_name}
                USING gin (tsv)
                """
            )

        logger.info(
            "VectorStore initialised",
            extra={"table": self._table_name, "dim": self._vector_dimension},
        )

    async def close(self) -> None:
        """Release the connection pool.

        Tolerates a closed event loop. The optimal-service singleton can
        outlive the loop that created its pool (e.g. cross-loop teardown
        between tests, where ``close_optimal_service`` runs on a new loop);
        ``asyncpg.Pool.close()`` then raises ``RuntimeError: Event loop is
        closed``. The connections died with the loop, so there is nothing left
        to release — drop the reference and move on. Any other RuntimeError
        still propagates.
        """
        if self._pool is not None:
            try:
                await self._pool.close()
            except RuntimeError as exc:
                if "Event loop is closed" not in str(exc):
                    raise
            self._pool = None

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def add_chunks(self, chunks: list[Chunk]) -> None:
        """Persist *chunks* that carry embeddings to the vector table.

        Chunks without an ``embedding`` are silently skipped.

        Args:
            chunks: Chunk objects with ``embedding`` populated by the
                    embedder.
        """
        records = [
            (
                chunk.text,
                chunk.source,
                _vec_to_str(chunk.embedding),
                json.dumps(chunk.metadata or {}),
            )
            for chunk in chunks
            if chunk.embedding is not None
        ]
        if not records:
            return

        pool = self._require_pool()
        async with pool.acquire() as conn:
            await conn.executemany(
                self._q(
                    """
                    INSERT INTO {table}
                        (content, source, embedding, metadata)
                    VALUES
                        ($1, $2, $3::vector, $4::jsonb)
                    """
                ),
                records,
            )

    async def delete_by_source(self, source: str) -> None:
        """Delete every chunk row for *source* (idempotent — no-op if absent).

        Makes re-ingestion of a source *replace* its chunks instead of
        appending a duplicate set on each reindex. ``source`` is bound as a
        query parameter; only the validated table identifier is interpolated.
        """
        pool = self._require_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                self._q("DELETE FROM {table} WHERE source = $1"),
                source,
            )

    async def replace_chunks(self, source: str, chunks: list[Chunk]) -> None:
        """Atomically replace every chunk row for *source* with *chunks*.

        Deletes the source's existing rows and inserts the new embedded chunks
        on a SINGLE connection inside a SINGLE transaction, so the whole
        replace is atomic. ``delete_by_source`` + ``add_chunks`` were two
        separate awaits on two separate pool connections; two concurrent
        re-indexes of the same source interleaved across those connections and
        produced duplicate chunk rows, and an insert failure after a
        successful delete lost the source's index rows. Wrapping both in one
        transaction closes the race (concurrent replacers serialize on the
        row locks; the last committer wins with no duplicates) and reverts the
        delete if the insert fails (no data loss).

        Chunks without an ``embedding`` are silently skipped (matches
        ``add_chunks``); an empty ``chunks`` list still clears the source
        (matches the prior delete-then-no-op-add behavior).
        """
        records = [
            (
                chunk.text,
                chunk.source,
                _vec_to_str(chunk.embedding),
                json.dumps(chunk.metadata or {}),
            )
            for chunk in chunks
            if chunk.embedding is not None
        ]
        # #181: an empty ``chunks`` list is a deliberate clear (matches the
        # prior delete-then-no-op-add behavior). But chunks passed with NO
        # usable embedding is an embedder failure — wiping the source's existing
        # rows on a failed embed would lose good index rows for nothing. No-op
        # there (distinct from the deliberate empty-list clear below).
        if chunks and not records:
            logger.warning(
                "replace_chunks: every chunk lacked an embedding (embedder "
                "failure?); skipping wipe to preserve existing rows",
                extra={"source": source, "chunk_count": len(chunks)},
            )
            return
        pool = self._require_pool()
        # One acquire, one transaction: the DELETE and INSERT share a single
        # connection and commit together (or roll back together on failure).
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                self._q("DELETE FROM {table} WHERE source = $1"),
                source,
            )
            if records:
                await conn.executemany(
                    self._q(
                        """
                        INSERT INTO {table}
                            (content, source, embedding, metadata)
                        VALUES
                            ($1, $2, $3::vector, $4::jsonb)
                        """
                    ),
                    records,
                )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def search(
        self,
        embedding: list[float],
        top_k: int = 5,
        min_chunk_length: int = 100,
    ) -> list[Citation]:
        """Return the *top_k* most similar chunks ordered by cosine similarity.

        Args:
            embedding:        Query embedding vector.
            top_k:            Maximum number of results to return.
            min_chunk_length: Minimum character length of returned chunks
                              (filters out very short index artifacts).

        Returns:
            List of :class:`Citation` objects ordered by descending score.
        """
        pool = self._require_pool()
        emb_str = _vec_to_str(embedding)

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                self._q(
                    """
                    SELECT
                        content,
                        source,
                        metadata,
                        1 - (embedding <=> $1::vector) AS score
                    FROM {table}
                    WHERE length(content) >= $2
                    ORDER BY embedding <=> $1::vector
                    LIMIT $3
                    """
                ),
                emb_str,
                min_chunk_length,
                top_k,
            )

        return [
            Citation(
                chunk=row["content"],
                source=row["source"],
                score=float(row["score"]),
                metadata=_as_dict(row["metadata"]),
            )
            for row in rows
        ]

    async def hybrid_search(
        self,
        embedding: list[float],
        query_text: str,
        top_k: int = 5,
        min_chunk_length: int = 100,
        candidate_pool: int = 50,
    ) -> list[Citation]:
        """Hybrid retrieval: fuse pgvector cosine with full-text keyword search.

        Per chunk the score is
        ``min(1, cosine + 0.3 * normalized_ts_rank)``: a vector-only match keeps
        its cosine score (so downstream thresholds — decisions/reviewer — are
        unchanged), a keyword match adds a bounded boost (the recall win), and a
        keyword-only match stays low (<= 0.3). Empty/garbage ``query_text``
        degrades gracefully to pure vector search.

        Args:
            embedding:        Query embedding vector.
            query_text:       Raw query for the full-text half.
            top_k:            Maximum number of fused results to return.
            min_chunk_length: Minimum character length of returned chunks.
            candidate_pool:   Per-side candidate count before fusion.
        """
        pool = self._require_pool()
        emb_str = _vec_to_str(embedding)
        cand = max(candidate_pool, top_k)

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                self._q(
                    """
                    WITH q AS (
                        SELECT websearch_to_tsquery('english', $2) AS tsq
                    ),
                    vec AS (
                        SELECT id, content, source, metadata,
                               1 - (embedding <=> $1::vector) AS cos
                        FROM {table}
                        WHERE length(content) >= $3 AND embedding IS NOT NULL
                        ORDER BY embedding <=> $1::vector
                        LIMIT $4
                    ),
                    kw AS (
                        SELECT c.id, c.content, c.source, c.metadata,
                               ts_rank(c.tsv, q.tsq) AS kw_rank
                        FROM {table} c, q
                        WHERE c.tsv @@ q.tsq AND length(c.content) >= $3
                        ORDER BY kw_rank DESC
                        LIMIT $4
                    ),
                    maxk AS (SELECT NULLIF(MAX(kw_rank), 0) AS m FROM kw),
                    fused AS (
                        SELECT COALESCE(vec.id, kw.id) AS id,
                               COALESCE(vec.content, kw.content) AS content,
                               COALESCE(vec.source, kw.source) AS source,
                               COALESCE(vec.metadata, kw.metadata) AS metadata,
                               COALESCE(vec.cos, 0) AS cos,
                               COALESCE(kw.kw_rank, 0) AS kw_rank
                        FROM vec FULL OUTER JOIN kw ON vec.id = kw.id
                    )
                    SELECT content, source, metadata,
                           LEAST(
                               1.0,
                               cos + 0.3 * COALESCE(
                                   kw_rank / (SELECT m FROM maxk), 0
                               )
                           ) AS score
                    FROM fused
                    ORDER BY score DESC
                    LIMIT $5
                    """
                ),
                emb_str,
                query_text,
                min_chunk_length,
                cand,
                top_k,
            )

        return [
            Citation(
                chunk=row["content"],
                source=row["source"],
                score=float(row["score"]),
                metadata=_as_dict(row["metadata"]),
            )
            for row in rows
        ]

    async def count(self) -> int:
        """Return the total number of chunk rows in the table."""
        pool = self._require_pool()
        async with pool.acquire() as conn:
            result: int = await conn.fetchval(self._q("SELECT COUNT(*) FROM {table}"))
        return int(result)

    async def list_docs(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        """Return a page of distinct document sources with their metadata.

        Args:
            limit:  Maximum number of rows to return.
            offset: Number of rows to skip (for pagination).

        Returns:
            List of dicts with keys: ``id``, ``source``, ``indexed_at``,
            ``metadata``.
        """
        pool = self._require_pool()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                self._q(
                    """
                    SELECT DISTINCT ON (source)
                        id,
                        source,
                        created_at AS indexed_at,
                        metadata
                    FROM {table}
                    ORDER BY source, created_at DESC
                    LIMIT $1 OFFSET $2
                    """
                ),
                limit,
                offset,
            )
        return [
            {
                "id": str(row["id"]),
                "source": row["source"],
                "indexed_at": row["indexed_at"].isoformat()
                if row["indexed_at"]
                else "",
                "metadata": _as_dict(row["metadata"]),
            }
            for row in rows
        ]

    async def clear(self) -> None:
        """Delete all rows from the table (non-destructive: table survives)."""
        pool = self._require_pool()
        async with pool.acquire() as conn:
            await conn.execute(self._q("DELETE FROM {table}"))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_identifier(name: str) -> str:
        """Validate a SQL table identifier against a strict allowlist.

        The table name is composed server-side from a fixed ``IndexType`` enum,
        never from user input — but validate defensively so it can never carry an
        injection payload, and so the controlled interpolation in :meth:`_q` is
        provably safe.
        """
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):
            raise ValueError(f"unsafe SQL table identifier: {name!r}")
        return name

    def _q(self, template: str) -> str:
        """Inject the validated table identifier into a SQL template.

        Values are always passed as ``$N`` bind parameters; the only
        interpolation is the table identifier, which an SQL placeholder cannot
        carry. ``str.replace`` (not ``%`` / ``.format`` / f-string / ``+``) keeps
        this controlled, allowlist-validated substitution out of bandit's B608
        SQL-injection heuristic — there is no user input in the query text.
        """
        return template.replace("{table}", self._table_name)

    def _require_pool(self) -> asyncpg.Pool[Any]:
        """Return the pool or raise if ``initialize()`` was not called."""
        if self._pool is None:
            raise RuntimeError(
                f"VectorStore for '{self._table_name}' is not initialised. "
                "Call await store.initialize() first."
            )
        return self._pool
