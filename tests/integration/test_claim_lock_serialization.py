"""Real-Postgres proof that ``TaskService.acquire_claim_lock`` serializes
concurrent claims by the SAME agent (one-task-per-agent) while NOT serializing
different agents. Skips when Postgres is unreachable; each ``acquire_claim_lock``
uses a throwaway session so cancellation mid-checkout can't poison the rest of
the test.
"""

from __future__ import annotations

import asyncio
import contextlib
from uuid import uuid4

import pytest
from roboco.services.task import TaskService
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


async def _fresh_session(url: str) -> tuple[AsyncSession, AsyncEngine]:
    """A session on a brand-new engine (caller disposes via ``_dispose``)."""
    engine = create_async_engine(url, future=True)
    factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    return factory(), engine


async def _dispose(session: AsyncSession, engine: AsyncEngine) -> None:
    with contextlib.suppress(Exception):
        await session.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_advisory_lock_serializes_same_agent_not_different(
    _test_database_url: str,
) -> None:
    """TX1 holds agent A's lock (uncommitted). A second acquire for agent A
    must block past a short deadline; an acquire for a DIFFERENT agent B must
    return immediately. After TX1 rolls back, agent A's lock is releasable.
    Proves the SQL is valid against real PG, serializes per-agent, is
    collision-free across agents, and is transaction-scoped (releases on
    rollback)."""
    url = _test_database_url
    agent_a = uuid4()
    agent_b = uuid4()

    # TX1 acquires agent A's tx-scoped advisory lock and holds it (no commit).
    holder, holder_engine = await _fresh_session(url)
    try:
        await TaskService(holder).acquire_claim_lock(agent_a)

        # A second transaction acquiring the SAME agent's lock must block.
        blocked, blocked_engine = await _fresh_session(url)
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(
                    TaskService(blocked).acquire_claim_lock(agent_a), timeout=0.5
                )
        finally:
            await _dispose(blocked, blocked_engine)

        # A different agent's lock is uncontended — acquires immediately.
        other, other_engine = await _fresh_session(url)
        try:
            await asyncio.wait_for(
                TaskService(other).acquire_claim_lock(agent_b), timeout=2.0
            )
        finally:
            await _dispose(other, other_engine)

        # Rolling back TX1 ends its transaction → releases the tx-scoped lock.
        await holder.rollback()
    finally:
        await _dispose(holder, holder_engine)

    # Agent A's lock is now free — a fresh transaction acquires it at once.
    reacquire, reacquire_engine = await _fresh_session(url)
    try:
        await asyncio.wait_for(
            TaskService(reacquire).acquire_claim_lock(agent_a), timeout=2.0
        )
    finally:
        await _dispose(reacquire, reacquire_engine)


@pytest.mark.asyncio
async def test_acquire_runs_without_error(_test_database_url: str) -> None:
    """Sanity: the advisory-lock SQL executes against the migrated schema
    (``hashtextextended`` exists, the statement is valid) — pins that the
    one-liner hasn't been broken by a schema/PG-version drift."""
    session, engine = await _fresh_session(_test_database_url)
    try:
        await TaskService(session).acquire_claim_lock(uuid4())
    finally:
        await _dispose(session, engine)
