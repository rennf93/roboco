"""A database built by the migration chain must match the models.

The suite builds its schema with ``Base.metadata.create_all`` (see
tests/conftest.py), and ``make quality`` only checks that the chain renders
as SQL (``alembic upgrade head --sql``), so nothing compared a migrated
database with ``roboco/db/tables.py``. Nine differences piled up unseen that
way, in indexes and unique keys a query or an ``ON CONFLICT`` relies on.

This builds a fresh database with ``alembic upgrade head`` (the path every
deployed database takes) and fails on any difference alembic's autogenerate
comparison reports, with ``compare_type`` on as in ``alembic/env.py``. It also
compares which indexes are partial, since autogenerate ignores an index's
WHERE clause (the predicate's text is not compared). The SAWarning about the
agents/products/projects/tasks/work_sessions foreign-key cycle concerns only
the order tables are walked in; their foreign keys are still compared.

Server defaults are not compared yet: many migrations set column defaults
that the models leave to a Python-side ``default=``, and reconciling those
is a separate change.

On a difference, fix the side that is wrong: the model when the migration
built what the code needs, or a new migration when the model is right.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from roboco.db.base import Base
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from tests.conftest import (
    _PG_AVAILABLE,
    _TEST_DB_ADMIN_DB,
    _TEST_DB_HOST,
    _TEST_DB_PASSWORD,
    _TEST_DB_PORT,
    _TEST_DB_USER,
    _build_url,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.engine import Connection

_REPO_ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.skipif(
    not _PG_AVAILABLE,
    reason=f"Postgres unreachable at {_TEST_DB_HOST}:{_TEST_DB_PORT}",
)


async def _admin_execute(statement: str) -> None:
    admin = await asyncpg.connect(
        host=_TEST_DB_HOST,
        port=_TEST_DB_PORT,
        user=_TEST_DB_USER,
        password=_TEST_DB_PASSWORD or None,
        database=_TEST_DB_ADMIN_DB,
    )
    try:
        await admin.execute(statement)
    finally:
        await admin.close()


def _migrate_to_head(db_name: str) -> None:
    """Run the chain in its own process: alembic/env.py reads the database
    from ``roboco.config.settings`` and reconfigures logging from
    alembic.ini, neither of which should touch this test process."""
    env = {
        **os.environ,
        "ROBOCO_DATABASE_HOST": _TEST_DB_HOST,
        "ROBOCO_DATABASE_PORT": str(_TEST_DB_PORT),
        "ROBOCO_DATABASE_USER": _TEST_DB_USER,
        "ROBOCO_DATABASE_PASSWORD": _TEST_DB_PASSWORD,
        "ROBOCO_DATABASE_NAME": db_name,
    }
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"alembic upgrade head failed:\n{result.stderr}"


@pytest_asyncio.fixture
async def migrated_database_url() -> AsyncIterator[str]:
    db_name = f"roboco_migrated_{os.getpid()}_{uuid4().hex[:8]}"
    await _admin_execute(f'CREATE DATABASE "{db_name}"')
    try:
        await asyncio.to_thread(_migrate_to_head, db_name)
        yield _build_url(db_name)
    finally:
        await _admin_execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            f"WHERE datname = '{db_name}' AND pid <> pg_backend_pid()"
        )
        await _admin_execute(f'DROP DATABASE IF EXISTS "{db_name}"')


def _autogenerate_differences(connection: Connection) -> list[Any]:
    context = MigrationContext.configure(connection, opts={"compare_type": True})
    return list(compare_metadata(context, Base.metadata))


def _partial_index_names(connection: Connection) -> set[str]:
    rows = connection.execute(
        text(
            "SELECT index_class.relname FROM pg_index "
            "JOIN pg_class AS index_class ON index_class.oid = pg_index.indexrelid "
            "JOIN pg_namespace ON pg_namespace.oid = index_class.relnamespace "
            "WHERE pg_index.indpred IS NOT NULL "
            "AND pg_namespace.nspname = current_schema()"
        )
    )
    return set(rows.scalars())


def _model_partial_index_names() -> set[str]:
    return {
        str(index.name)
        for table in Base.metadata.tables.values()
        for index in table.indexes
        if index.dialect_options["postgresql"]["where"] is not None
    }


async def _read_schema(url: str) -> tuple[list[Any], set[str]]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            differences = await connection.run_sync(_autogenerate_differences)
            partial = await connection.run_sync(_partial_index_names)
    finally:
        await engine.dispose()
    return differences, partial


async def test_a_migrated_database_matches_the_models(
    migrated_database_url: str,
) -> None:
    differences, migrated_partial = await _read_schema(migrated_database_url)

    assert not differences, (
        "alembic upgrade head and roboco/db/tables.py disagree "
        "(remove_* = only the database has it, add_* = only the model has it):\n"
        + "\n".join(repr(difference) for difference in differences)
    )
    assert migrated_partial == _model_partial_index_names(), (
        "partial indexes differ between the migrated database and the models: "
        f"database-only {sorted(migrated_partial - _model_partial_index_names())}, "
        f"model-only {sorted(_model_partial_index_names() - migrated_partial)}"
    )
