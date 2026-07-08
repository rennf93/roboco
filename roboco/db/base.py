"""
Database base configuration and session management.
"""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import structlog
from alembic import command
from alembic.config import Config
from fastapi import Depends, Request
from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from roboco.config import settings

logger = structlog.get_logger()

# Naming convention for constraints (helps with migrations)
convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    metadata = MetaData(naming_convention=convention)


class _DbHolder:
    """Holder for database engine and session factory singletons."""

    engine: AsyncEngine | None = None
    session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Get or create the async engine."""
    if _DbHolder.engine is None:
        _DbHolder.engine = create_async_engine(
            settings.database_url,
            echo=settings.database_echo,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout=settings.database_pool_timeout,
            pool_recycle=settings.database_pool_recycle,
            pool_pre_ping=True,
        )
    return _DbHolder.engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Get or create the async session factory."""
    if _DbHolder.session_factory is None:
        _DbHolder.session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _DbHolder.session_factory


async def get_db() -> AsyncGenerator[AsyncSession]:
    """
    Dependency for FastAPI routes.

    Usage:
        @router.get("/items")
        async def get_items(db: AsyncSession = Depends(get_db)):
            ...

    Also called directly (no ``Depends``, outside HTTP request scope) by
    ``websocket.py`` and a couple of read-only helpers — keep this signature
    free of any required/HTTP-only parameter. For a request-scoped route that
    wants its commit to land BEFORE the response reaches the client, depend
    on ``get_db_committed`` instead (``roboco.api.deps.DbSession`` — the one
    place every route already goes through — already does).
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except asyncio.CancelledError:
            await _discard_on_cancel(session)
            raise
        except Exception:
            await session.rollback()
            raise


async def _discard_on_cancel(session: AsyncSession) -> None:
    """Discard (never reuse) a session cancelled mid-flight.

    A server-side ``asyncio.timeout`` (``FlowVerbTimeoutMiddleware``) can fire
    while the session is mid ``await`` on a real DBAPI round-trip — not just
    while idle holding a ``FOR UPDATE`` lock, but also mid-``commit()``
    (``DbCommitMiddleware`` runs its own commit in the ASGI send path, still
    inside the same cancellable scope). Cancelling a greenlet-bridged asyncpg
    operation mid-flight leaves the connection's wire-protocol state
    undefined; SQLAlchemy's own docs (``Session.invalidate``) prescribe
    exactly this: on a Timeout/cancellation, invalidate rather than rollback,
    since rollback() itself would issue another command over a connection
    that may already be desynced, and a desynced connection returned to the
    pool is what later corrupted a *different* request's checkout (the
    uvloop/asyncpg segfault class this fixes). A plain hang (asyncio.sleep,
    no DBAPI call in flight when cancelled) is also safe to invalidate — just
    slightly more heavy-handed than the rollback it used to get.
    """
    try:
        await session.invalidate()
    except Exception as e:
        # The connection is already being discarded; a failure tearing it
        # down further (SQLAlchemy's own pool logs the underlying cause) must
        # not mask the CancelledError the caller is propagating.
        logger.debug("Session invalidate-on-cancel raised", error=str(e))


async def get_db_committed(
    request: Request, db: Annotated[AsyncSession, Depends(get_db)]
) -> AsyncGenerator[AsyncSession]:
    """FastAPI-only wrapper around ``get_db``: stashes the live session on
    ``request.state.db_session`` so ``DbCommitMiddleware``
    (``roboco/api/middleware.py``) can commit it BEFORE the response reaches
    the client.

    FastAPI resolves ``Depends(get_db)`` on the request-scoped exit stack, and
    its routing sends the response to the client BEFORE that stack unwinds —
    so ``get_db``'s post-yield ``commit()`` used to land after a 200 already
    went out.

    This is a separate function rather than a ``request`` parameter added to
    ``get_db`` itself: FastAPI only special-cases a dependency parameter
    typed exactly ``Request`` (``lenient_issubclass`` in
    ``fastapi/dependencies/utils.py``) — a ``Request | None`` union is NOT
    special-cased and instead gets validated as a Pydantic response field,
    which crashes route registration outright (``Request`` isn't a valid
    Pydantic field type). ``get_db`` is also called directly with no request
    in scope, so its signature has to stay request-free; this wrapper is the
    request-scoped variant, resolved once per request (FastAPI dependency
    caching) so ``db`` here is the exact same session ``get_db`` yields.
    """
    request.state.db_session = db
    yield db


@asynccontextmanager
async def get_db_context() -> AsyncGenerator[AsyncSession]:
    """
    Context manager for database sessions outside of FastAPI.

    Usage:
        async with get_db_context() as db:
            result = await db.execute(...)
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except asyncio.CancelledError:
            await _discard_on_cancel(session)
            raise
        except Exception:
            await session.rollback()
            raise


async def _db_has_tables(conn: AsyncConnection) -> bool:
    """Does the DB have any application tables (excluding alembic_version)?"""
    result = await conn.execute(
        text(
            "SELECT EXISTS ("
            "  SELECT 1 FROM information_schema.tables "
            "  WHERE table_schema = 'public' "
            "    AND table_name NOT IN ('alembic_version')"
            ")"
        )
    )
    return bool(result.scalar())


async def _db_has_alembic_version(conn: AsyncConnection) -> bool:
    result = await conn.execute(
        text(
            "SELECT EXISTS ("
            "  SELECT 1 FROM information_schema.tables "
            "  WHERE table_schema = 'public' AND table_name = 'alembic_version'"
            ")"
        )
    )
    return bool(result.scalar())


# Hard ceiling on the alembic worker thread. Its env.py nests asyncio.run +
# a fresh NullPool engine + a greenlet bridge inside a (possibly reused)
# executor thread — a hang there previously blocked the API bind forever
# (2026-07-08 NAS outage: two consecutive boots stuck after the alembic
# context lines with zero SQL activity). A timeout can't kill the thread,
# but failing loud lets the container restart into a clean retry.
_ALEMBIC_TIMEOUT_SECONDS = 300


async def run_migrations() -> None:
    """
    Apply Alembic migrations up to head.

    Auto-stamps pre-migration DBs (schema created by a prior `create_all` run
    with no `alembic_version`) at the initial revision so subsequent migrations
    (e.g. adding an enum value) apply cleanly instead of re-running 001.

    Runs in a worker thread — Alembic's command.upgrade uses the sync SA API.
    """
    # First: decide if we need to stamp. Done with the async engine so we
    # don't need a second sync-DB round-trip inside the worker thread.
    engine = get_engine()
    needs_stamp = False
    async with engine.connect() as conn:
        has_version = await _db_has_alembic_version(conn)
        if not has_version:
            has_tables = await _db_has_tables(conn)
            needs_stamp = has_tables

    ini_path = Path(__file__).resolve().parents[2] / "alembic.ini"
    initial_revision = "001_initial_schema"

    def _run_alembic() -> None:
        cfg = Config(str(ini_path))
        cfg.set_main_option("sqlalchemy.url", settings.database_url_sync)
        if needs_stamp:
            # Existing schema from create_all — mark 001 as applied so the
            # upgrade that follows only runs 002+.
            logger.info(
                "Stamping pre-migration DB at initial revision",
                revision=initial_revision,
            )
            command.stamp(cfg, initial_revision)
        logger.info("Alembic upgrade starting")
        command.upgrade(cfg, "head")
        logger.info("Alembic upgrade finished")

    try:
        await asyncio.wait_for(
            asyncio.to_thread(_run_alembic), timeout=_ALEMBIC_TIMEOUT_SECONDS
        )
    except TimeoutError as e:
        raise RuntimeError(
            f"alembic migration runner exceeded {_ALEMBIC_TIMEOUT_SECONDS}s — "
            "worker thread wedged (nested asyncio.run in alembic/env.py); "
            "failing startup loudly instead of hanging the API bind"
        ) from e


class _InitState:
    """Per-process init_db latch, keyed by database URL (see init_db docstring).

    URL-keyed so a process that initializes a DIFFERENT database (test
    fixtures build throwaway DBs) always runs in full; only a repeat call
    for the same database no-ops.
    """

    completed_url: str | None = None


async def init_db() -> None:
    """
    Initialize the database schema by running the Alembic migration chain.

    Migration 017 reconciled the chain to reproduce the FULL ORM schema, so
    `alembic upgrade head` from base now builds every table/column/index AND
    runs migration-embedded SEED DATA — e.g. the AI providers seeded in 004.
    Building a fresh DB with a bare `create_all` (as a prior version did) skips
    that seed: that is why a DB reset left `provider_configs` empty and the
    Ollama-key endpoint 404'd. Always running migrations restores the seeds.

    Fresh DB  -> run the chain from base: full schema + seed data.
    Existing  -> apply pending migrations (a genuine failure is RAISED, not
                 masked by a silent fallback), then `create_all(checkfirst=True)`
                 to gap-fill any ORM table a migration didn't create.
                 `create_all` cannot ALTER an existing table, so an ORM column
                 added without a migration needs a fresh rebuild to appear.

    Idempotent per process: bootstrap and the API lifespan both call this in
    the same interpreter seconds apart; the second call re-entered the fragile
    alembic-in-thread machinery for zero benefit and hung the 2026-07-08 NAS
    boot twice. A completed run latches, later calls no-op. drop_db resets the
    latch so tests rebuilding the schema keep working.
    """
    if _InitState.completed_url == settings.database_url:
        logger.info("init_db already completed in this process — skipping")
        return
    engine = get_engine()
    async with engine.begin() as conn:
        # pgvector must exist before tables that use the vector type
        try:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            logger.info("pgvector extension enabled")
        except Exception as e:
            logger.warning(
                "pgvector extension not available - RAG features will be disabled",
                error=str(e),
            )

    async with engine.connect() as conn:
        has_tables = await _db_has_tables(conn)

    # Run the chain in both cases. run_migrations() stamps a pre-Alembic
    # create_all DB at the initial revision first; a genuine failure propagates.
    await run_migrations()

    if has_tables:
        # Existing DB: gap-fill any ORM table a migration didn't create.
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Existing DB: migrations applied + create_all gap-fill")
    else:
        logger.info("Fresh DB: built via migrations (full schema + seed data)")

    # Dispose the async engine's connection pool. asyncpg caches enum type
    # OIDs and their values at connection-establishment time; any connection
    # opened BEFORE a migration that ran `ALTER TYPE ... ADD VALUE` will
    # reject the new value as if it didn't exist. Disposing forces every
    # subsequent request to introspect the current (post-migration) schema.
    await engine.dispose()
    logger.info("DB engine pool disposed to refresh asyncpg type cache")
    _InitState.completed_url = settings.database_url


async def drop_db() -> None:
    """
    Drop all tables.

    DANGEROUS: Only use in development/testing.
    """
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    _InitState.completed_url = None


async def close_db() -> None:
    """Close the database connection."""
    if _DbHolder.engine is not None:
        await _DbHolder.engine.dispose()
        _DbHolder.engine = None
        _DbHolder.session_factory = None
