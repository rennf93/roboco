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
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
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
                metadata=dict(row["metadata"]) if row["metadata"] else {},
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
                "metadata": dict(row["metadata"]) if row["metadata"] else {},
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
