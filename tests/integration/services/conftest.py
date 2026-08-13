"""Savepoint-isolated ``db_session`` override for this directory.

The background engines under test here (dep-update / ci-watch / env-sync)
deliberately commit mid-``run_cycle`` now, to release the pool connection
before a slow probe (git clone, GitHub HTTP call, merges-API call — the
2026-07-29 pool-exhaustion fix). The root ``db_session`` fixture
(``tests/conftest.py``) isolates tests with a plain rollback at teardown,
which only undoes UNCOMMITTED state — a mid-test commit would otherwise
leak rows into the shared session-scoped test database and pollute any
later test in the same run (e.g. an unscoped ``list_open_*_tasks()`` count).

This override nests the whole test inside one real connection-level
transaction and gives the session a SAVEPOINT via
``join_transaction_mode="create_savepoint"``: every ``commit()`` the code
under test issues only ends the savepoint (a fresh one restarts
automatically on the next statement), while the real transaction is what
gets rolled back at teardown — nothing committed inside a test ever
survives it. ``session.in_transaction()`` still correctly reports False
right after such a commit, so pool-release regression tests that assert on
it are unaffected. Behavior-identical to the root fixture for any test that
never commits (a savepoint rollback undoes a plain flush the same way).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest_asyncio.fixture
async def db_session(_test_database_url: str) -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(_test_database_url, future=True)
    async with engine.connect() as connection:
        await connection.begin()
        factory = async_sessionmaker(
            bind=connection,
            class_=AsyncSession,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        async with factory() as session:
            try:
                yield session
            finally:
                await session.close()
        await connection.rollback()
    await engine.dispose()
