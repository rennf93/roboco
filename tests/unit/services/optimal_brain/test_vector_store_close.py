"""VectorStore.close() must tolerate a closed event loop.

The optimal-service singleton can outlive the loop that created its asyncpg
pool (cross-loop teardown between tests). asyncpg's ``pool.close()`` then raises
``RuntimeError: Event loop is closed``; the connections are already gone, so
close() drops the pool instead of propagating. Any other RuntimeError still
propagates.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
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
