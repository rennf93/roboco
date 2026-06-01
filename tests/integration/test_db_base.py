"""roboco.db.base coverage — engine/session helpers + migration runner.

Covers the DB lifecycle helpers — get_engine/get_session_factory caching,
get_db / get_db_context async generators (commit-on-success, rollback-on-
error), run_migrations stamp logic, init_db pgvector + fallback paths,
and drop/close.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.db.base import (
    _db_has_alembic_version,
    _db_has_tables,
    _DbHolder,
    close_db,
    drop_db,
    get_db,
    get_db_context,
    get_engine,
    get_session_factory,
    init_db,
    run_migrations,
)


@pytest.fixture(autouse=True)
def _reset_holder():
    """Snapshot/restore the singleton so tests don't poison the live engine."""
    saved_engine = _DbHolder.engine
    saved_factory = _DbHolder.session_factory
    yield
    _DbHolder.engine = saved_engine
    _DbHolder.session_factory = saved_factory


# ---------------------------------------------------------------------------
# get_engine / get_session_factory
# ---------------------------------------------------------------------------


def test_get_engine_creates_and_caches() -> None:
    _DbHolder.engine = None
    fake_engine = MagicMock()
    with patch("roboco.db.base.create_async_engine", return_value=fake_engine) as ce:
        eng1 = get_engine()
        eng2 = get_engine()
    assert eng1 is fake_engine
    assert eng2 is fake_engine
    # create_async_engine called only once.
    assert ce.call_count == 1


def test_get_session_factory_creates_and_caches() -> None:
    _DbHolder.engine = None
    _DbHolder.session_factory = None
    fake_engine = MagicMock()
    fake_factory = MagicMock()
    with (
        patch("roboco.db.base.create_async_engine", return_value=fake_engine),
        patch("roboco.db.base.async_sessionmaker", return_value=fake_factory) as sm,
    ):
        f1 = get_session_factory()
        f2 = get_session_factory()
    assert f1 is fake_factory
    assert f2 is fake_factory
    assert sm.call_count == 1


# ---------------------------------------------------------------------------
# get_db (FastAPI dependency)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_db_yields_session_and_commits_on_success() -> None:
    """Happy path: yield, commit on close."""
    fake_session = MagicMock()
    fake_session.commit = AsyncMock()
    fake_session.rollback = AsyncMock()

    class _Cm:
        async def __aenter__(self) -> object:
            return fake_session

        async def __aexit__(self, *_args: object) -> None:
            return None

    fake_factory = MagicMock(return_value=_Cm())

    with patch("roboco.db.base.get_session_factory", return_value=fake_factory):
        gen = get_db()
        s = await gen.__anext__()
        assert s is fake_session
        # Drive the generator to its end — triggers the commit branch.
        with pytest.raises(StopAsyncIteration):
            await gen.__anext__()

    fake_session.commit.assert_awaited_once()
    fake_session.rollback.assert_not_called()


@pytest.mark.asyncio
async def test_get_db_rolls_back_on_exception() -> None:
    fake_session = MagicMock()
    fake_session.commit = AsyncMock()
    fake_session.rollback = AsyncMock()

    class _Cm:
        async def __aenter__(self) -> object:
            return fake_session

        async def __aexit__(self, *_args: object) -> None:
            return None

    fake_factory = MagicMock(return_value=_Cm())

    with patch("roboco.db.base.get_session_factory", return_value=fake_factory):
        gen = get_db()
        await gen.__anext__()
        # Push an exception into the generator at the yield point.
        with pytest.raises(RuntimeError, match="boom"):
            await gen.athrow(RuntimeError("boom"))

    fake_session.rollback.assert_awaited_once()
    fake_session.commit.assert_not_called()


# ---------------------------------------------------------------------------
# get_db_context (asynccontextmanager wrapper)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_db_context_commits_on_success() -> None:
    fake_session = MagicMock()
    fake_session.commit = AsyncMock()
    fake_session.rollback = AsyncMock()

    class _Cm:
        async def __aenter__(self) -> object:
            return fake_session

        async def __aexit__(self, *_args: object) -> None:
            return None

    with patch(
        "roboco.db.base.get_session_factory", return_value=MagicMock(return_value=_Cm())
    ):
        async with get_db_context() as s:
            assert s is fake_session

    fake_session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_db_context_rolls_back_on_exception() -> None:
    fake_session = MagicMock()
    fake_session.commit = AsyncMock()
    fake_session.rollback = AsyncMock()

    class _Cm:
        async def __aenter__(self) -> object:
            return fake_session

        async def __aexit__(self, *_args: object) -> None:
            return None

    with (
        patch(
            "roboco.db.base.get_session_factory",
            return_value=MagicMock(return_value=_Cm()),
        ),
        pytest.raises(RuntimeError, match="boom"),
    ):
        async with get_db_context():
            raise RuntimeError("boom")

    fake_session.rollback.assert_awaited_once()
    fake_session.commit.assert_not_called()


# ---------------------------------------------------------------------------
# _db_has_tables / _db_has_alembic_version
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_db_has_tables_returns_true_when_present() -> None:
    fake_conn = MagicMock()
    fake_result = MagicMock()
    fake_result.scalar.return_value = True
    fake_conn.execute = AsyncMock(return_value=fake_result)
    assert (await _db_has_tables(fake_conn)) is True


@pytest.mark.asyncio
async def test_db_has_tables_returns_false_when_absent() -> None:
    fake_conn = MagicMock()
    fake_result = MagicMock()
    fake_result.scalar.return_value = False
    fake_conn.execute = AsyncMock(return_value=fake_result)
    assert (await _db_has_tables(fake_conn)) is False


@pytest.mark.asyncio
async def test_db_has_alembic_version_returns_true() -> None:
    fake_conn = MagicMock()
    fake_result = MagicMock()
    fake_result.scalar.return_value = True
    fake_conn.execute = AsyncMock(return_value=fake_result)
    assert (await _db_has_alembic_version(fake_conn)) is True


# ---------------------------------------------------------------------------
# run_migrations
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_migrations_no_stamp_when_alembic_version_present() -> None:
    """When alembic_version table exists, no stamp is needed."""
    fake_conn = MagicMock()
    fake_result = MagicMock()
    fake_result.scalar.return_value = True
    fake_conn.execute = AsyncMock(return_value=fake_result)

    class _ConnCm:
        async def __aenter__(self) -> object:
            return fake_conn

        async def __aexit__(self, *_args: object) -> None:
            return None

    fake_engine = MagicMock()
    fake_engine.connect = MagicMock(return_value=_ConnCm())

    with (
        patch("roboco.db.base.get_engine", return_value=fake_engine),
        patch("roboco.db.base.command") as cmd,
        patch("roboco.db.base.Config"),
        patch("roboco.db.base.asyncio.to_thread", new=AsyncMock()) as to_thread,
    ):
        await run_migrations()
        # The work runs in a thread.
        to_thread.assert_awaited_once()
        # Verify the inner function did not stamp (call directly).
        target = to_thread.call_args.args[0]
        target()
        cmd.upgrade.assert_called()
        cmd.stamp.assert_not_called()


@pytest.mark.asyncio
async def test_run_migrations_stamps_when_pre_migration_db() -> None:
    """When alembic_version is missing but tables exist, stamp first."""
    fake_conn = MagicMock()

    # Alternating returns: first call (alembic version) False, then tables True.
    call_idx = {"n": 0}

    def _exec_response(*_a: object, **_kw: object) -> object:
        call_idx["n"] += 1
        result = MagicMock()
        # First call returns False (no alembic_version)
        # Second call returns True (has user tables)
        result.scalar.return_value = call_idx["n"] != 1
        return result

    fake_conn.execute = AsyncMock(side_effect=_exec_response)

    class _ConnCm:
        async def __aenter__(self) -> object:
            return fake_conn

        async def __aexit__(self, *_args: object) -> None:
            return None

    fake_engine = MagicMock()
    fake_engine.connect = MagicMock(return_value=_ConnCm())

    with (
        patch("roboco.db.base.get_engine", return_value=fake_engine),
        patch("roboco.db.base.command") as cmd,
        patch("roboco.db.base.Config"),
        patch("roboco.db.base.asyncio.to_thread", new=AsyncMock()) as to_thread,
    ):
        await run_migrations()
        target = to_thread.call_args.args[0]
        target()
        cmd.stamp.assert_called_once()
        cmd.upgrade.assert_called()


# ---------------------------------------------------------------------------
# init_db
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_init_db_happy_path_runs_migrations() -> None:
    fake_conn = MagicMock()
    fake_conn.execute = AsyncMock()
    fake_conn.run_sync = AsyncMock()

    class _ConnCm:
        async def __aenter__(self) -> object:
            return fake_conn

        async def __aexit__(self, *_args: object) -> None:
            return None

    fake_engine = MagicMock()
    fake_engine.begin = MagicMock(return_value=_ConnCm())
    fake_engine.dispose = AsyncMock()

    with (
        patch("roboco.db.base.get_engine", return_value=fake_engine),
        patch("roboco.db.base._db_has_tables", new=AsyncMock(return_value=True)),
        patch("roboco.db.base.run_migrations", new=AsyncMock()) as rm,
    ):
        await init_db()

    rm.assert_awaited_once()
    fake_engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_init_db_pgvector_failure_is_swallowed() -> None:
    """If CREATE EXTENSION raises, init_db logs a warning and continues."""
    fake_conn = MagicMock()
    fake_conn.execute = AsyncMock(side_effect=RuntimeError("pgvector unavailable"))
    fake_conn.run_sync = AsyncMock()

    class _ConnCm:
        async def __aenter__(self) -> object:
            return fake_conn

        async def __aexit__(self, *_args: object) -> None:
            return None

    fake_engine = MagicMock()
    fake_engine.begin = MagicMock(return_value=_ConnCm())
    fake_engine.dispose = AsyncMock()

    with (
        patch("roboco.db.base.get_engine", return_value=fake_engine),
        patch("roboco.db.base._db_has_tables", new=AsyncMock(return_value=True)),
        patch("roboco.db.base.run_migrations", new=AsyncMock()),
    ):
        await init_db()

    fake_engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_init_db_raises_when_migrations_fail_on_existing_db() -> None:
    """A migration failure on an existing DB is RAISED, not masked.

    The old `create_all` fallback was removed (2026-06-01): it swallowed the
    error and, because create_all cannot ALTER an existing table, left the
    schema half-built (the products-table crash loop). Failing loud surfaces the
    real error instead.
    """
    fake_conn = MagicMock()
    fake_conn.execute = AsyncMock()
    fake_conn.run_sync = AsyncMock()

    class _ConnCm:
        async def __aenter__(self) -> object:
            return fake_conn

        async def __aexit__(self, *_args: object) -> None:
            return None

    fake_engine = MagicMock()
    fake_engine.begin = MagicMock(return_value=_ConnCm())
    fake_engine.connect = MagicMock(return_value=_ConnCm())
    fake_engine.dispose = AsyncMock()

    with (
        patch("roboco.db.base.get_engine", return_value=fake_engine),
        patch("roboco.db.base._db_has_tables", new=AsyncMock(return_value=True)),
        patch(
            "roboco.db.base.run_migrations",
            new=AsyncMock(side_effect=RuntimeError("alembic broken")),
        ),
        pytest.raises(RuntimeError, match="alembic broken"),
    ):
        await init_db()

    # No silent create_all fallback on the failure path.
    fake_conn.run_sync.assert_not_awaited()


@pytest.mark.asyncio
async def test_init_db_fresh_db_runs_migrations() -> None:
    """A fresh DB (no tables) is built by running the complete migration chain
    from base — NOT a bare create_all — so migration-embedded seed data (e.g. the
    AI providers seeded in 004) is inserted. (A bare create_all skipped the seed,
    which left provider_configs empty and 404'd the Ollama-key endpoint.)"""
    fake_conn = MagicMock()
    fake_conn.execute = AsyncMock()
    fake_conn.run_sync = AsyncMock()

    class _ConnCm:
        async def __aenter__(self) -> object:
            return fake_conn

        async def __aexit__(self, *_args: object) -> None:
            return None

    fake_engine = MagicMock()
    fake_engine.begin = MagicMock(return_value=_ConnCm())
    fake_engine.connect = MagicMock(return_value=_ConnCm())
    fake_engine.dispose = AsyncMock()

    with (
        patch("roboco.db.base.get_engine", return_value=fake_engine),
        patch("roboco.db.base._db_has_tables", new=AsyncMock(return_value=False)),
        patch("roboco.db.base.run_migrations", new=AsyncMock()) as rm,
    ):
        await init_db()

    rm.assert_awaited_once()  # full chain from base -> schema + seeds
    fake_conn.run_sync.assert_not_awaited()  # no bare create_all on the fresh path
    fake_engine.dispose.assert_awaited_once()


# ---------------------------------------------------------------------------
# drop_db / close_db
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_drop_db_runs_drop_all() -> None:
    fake_conn = MagicMock()
    fake_conn.run_sync = AsyncMock()

    class _ConnCm:
        async def __aenter__(self) -> object:
            return fake_conn

        async def __aexit__(self, *_args: object) -> None:
            return None

    fake_engine = MagicMock()
    fake_engine.begin = MagicMock(return_value=_ConnCm())

    with patch("roboco.db.base.get_engine", return_value=fake_engine):
        await drop_db()

    fake_conn.run_sync.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_db_disposes_and_clears_singletons() -> None:
    fake_engine = MagicMock()
    fake_engine.dispose = AsyncMock()
    _DbHolder.engine = fake_engine
    _DbHolder.session_factory = MagicMock()

    await close_db()

    fake_engine.dispose.assert_awaited_once()
    assert _DbHolder.engine is None
    assert _DbHolder.session_factory is None


@pytest.mark.asyncio
async def test_close_db_when_no_engine_is_noop() -> None:
    _DbHolder.engine = None
    _DbHolder.session_factory = None
    await close_db()  # Should not raise.
    assert _DbHolder.engine is None
