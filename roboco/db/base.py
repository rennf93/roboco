"""
Database base configuration and session management.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from roboco.config import settings

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


async def init_db() -> None:
    """
    Initialize the database (create all tables).

    Should only be used in development. Use Alembic for production.
    """
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


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
