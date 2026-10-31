"""VectorStore.close() must tolerate a closed event loop.

The optimal-service singleton can outlive the loop that created its asyncpg
pool (cross-loop teardown between tests). asyncpg's ``pool.close()`` then raises
``RuntimeError: Event loop is closed``; the connections are already gone, so
close() drops the pool instead of propagating. Any other RuntimeError still
propagates.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.services.optimal_brain import vector_store as vector_store_module
from roboco.services.optimal_brain.vector_store import VectorStore


@pytest.mark.asyncio
async def test_close_swallows_event_loop_closed() -> None:
    vs = VectorStore.__new__(VectorStore)
    pool = AsyncMock()
    pool.close = AsyncMock(side_effect=RuntimeError("Event loop is closed"))
    vs._pool = pool

    await vs.close()  # must not raise

    assert vs._pool is None


@pytest.mark.asyncio
async def test_close_propagates_other_runtime_errors() -> None:
    vs = VectorStore.__new__(VectorStore)
    pool = AsyncMock()
    pool.close = AsyncMock(side_effect=RuntimeError("connection refused"))
    vs._pool = pool

    with pytest.raises(RuntimeError, match="connection refused"):
        await vs.close()


@pytest.mark.asyncio
async def test_close_noop_when_no_pool() -> None:
    vs = VectorStore.__new__(VectorStore)
    vs._pool = None
    await vs.close()
    assert vs._pool is None


@pytest.mark.asyncio
async def test_initialize_closes_the_pool_when_provisioning_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A store whose table provisioning fails (or is cancelled) closes the pool
    it just opened: its index plugin is never registered, so nothing else
    would."""
    conn = MagicMock()
    conn.execute = AsyncMock(side_effect=RuntimeError("no pgvector"))
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    pool.close = AsyncMock()
    monkeypatch.setattr(
        vector_store_module.asyncpg, "create_pool", AsyncMock(return_value=pool)
    )
    vs = VectorStore("postgresql://x/y", "chunks_documentation", 8)

    with pytest.raises(RuntimeError, match="no pgvector"):
        await vs.initialize()

    pool.close.assert_awaited_once()
    assert vs._pool is None


def test_safe_identifier_accepts_valid_table_names() -> None:
    for name in ("chunks_documentation", "chunks_decisions", "chunks_journals"):
        assert VectorStore._safe_identifier(name) == name


def test_safe_identifier_rejects_injection_attempts() -> None:
    for bad in (
        "chunks; DROP TABLE users",
        "chunks documentation",
        "Chunks-Bad",
        "1chunks",
        "",
    ):
        with pytest.raises(ValueError, match="unsafe SQL table identifier"):
            VectorStore._safe_identifier(bad)


def test_q_injects_validated_table_identifier() -> None:
    vs = VectorStore.__new__(VectorStore)
    vs._table_name = "chunks_documentation"
    assert vs._q("SELECT COUNT(*) FROM {table}") == (
        "SELECT COUNT(*) FROM chunks_documentation"
    )
