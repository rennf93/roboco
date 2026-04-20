"""
Database base configuration and session management.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
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
    """
    session_factory = get_session_factory()
    async with session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


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


async def run_migrations() -> None:
    """
    Apply Alembic migrations up to head.

    Auto-stamps pre-migration DBs (schema created by a prior `create_all` run
    with no `alembic_version`) at the initial revision so subsequent migrations
    (e.g. adding an enum value) apply cleanly instead of re-running 001.

    Runs in a worker thread — Alembic's command.upgrade uses the sync SA API.
    """
    import asyncio
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    from roboco.config import settings

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
        command.upgrade(cfg, "head")

    await asyncio.to_thread(_run_alembic)


async def init_db() -> None:
    """
    Initialize the database: pgvector extension, then Alembic migrations, with
    a create_all fallback if the migration chain itself is broken.

    Migrations are authoritative — `create_all` alone cannot add enum values
    or alter existing objects, which is why notificationtype.APPROVAL was
    missing from live DBs even though it had been added to the Python enum.
    """
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

    try:
        await run_migrations()
        logger.info("Alembic migrations applied (head)")
    except Exception as e:
        logger.warning(
            "Alembic upgrade failed, falling back to create_all",
            error=str(e),
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Tables created via create_all fallback")

    # Dispose the async engine's connection pool. asyncpg caches enum type
    # OIDs and their values at connection-establishment time; any connection
    # opened BEFORE a migration that ran `ALTER TYPE ... ADD VALUE` will
    # reject the new value as if it didn't exist. Disposing forces every
    # subsequent request to introspect the current (post-migration) schema.
    await engine.dispose()
    logger.info("DB engine pool disposed to refresh asyncpg type cache")


async def drop_db() -> None:
    """
    Drop all tables.

    DANGEROUS: Only use in development/testing.
    """
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def close_db() -> None:
    """Close the database connection."""
    if _DbHolder.engine is not None:
        await _DbHolder.engine.dispose()
        _DbHolder.engine = None
        _DbHolder.session_factory = None
