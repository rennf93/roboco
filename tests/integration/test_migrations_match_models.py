"""Production's schema and the test suite's schema must be one schema.

Deployed databases are built by the migration chain (``alembic upgrade
head``); the test suite builds its own with ``Base.metadata.create_all``
(tests/conftest.py), and ``make quality`` only renders the chain as SQL.
Nothing compared the two, and they drifted: indexes and unique keys that a
query or an ``ON CONFLICT`` relies on, 106 server defaults, foreign-key
names, and a partial-index predicate the queries could not use.

Two checks against a fresh migrated database:

- alembic's autogenerate comparison with ``Base.metadata``, with
  ``compare_type`` and ``compare_server_default`` on as in ``alembic/env.py``,
  so ``make migration`` has nothing to propose;
- the catalog itself against a ``create_all`` database: every column's type,
  nullability and default, every index (``pg_get_indexdef``), every
  constraint (``pg_get_constraintdef``) and every enum's labels. This catches
  what autogenerate does not compare, such as constraint names, partial-index
  predicates and NULLS NOT DISTINCT.

Autogenerate warns that the agents/products/projects/tasks/work_sessions
foreign keys form a cycle; that only affects the order it walks the tables
in, and those foreign keys are still compared.

Enum labels are compared as sets, not in order. ``ALTER TYPE ... ADD VALUE``
appends, so a migrated enum's order is its history, and reordering one means
recreating the type and rewriting every table that uses it (``tasks``
included). The only SQL that sorts by an enum is
``ProductService.distinct_project_ids`` (``ORDER BY team``), over the three
cell teams, which come first, in the same order, either way.

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

pytestmark = [
    pytest.mark.skipif(
        not _PG_AVAILABLE,
        reason=f"Postgres unreachable at {_TEST_DB_HOST}:{_TEST_DB_PORT}",
    ),
    # Both databases are built once for the module, on the module's loop.
    pytest.mark.asyncio(loop_scope="module"),
]

# One row per schema object: (kind, key, definition). Views (the
# pg_stat_statements extension's) and alembic's own bookkeeping table are
# not part of the application schema.
_CATALOG = text(
    """
    SELECT 'column', c.relname || '.' || a.attname,
           format_type(a.atttypid, a.atttypmod)
           || CASE WHEN a.attnotnull THEN ' NOT NULL' ELSE '' END
           || coalesce(' DEFAULT ' || pg_get_expr(d.adbin, d.adrelid), '')
    FROM pg_attribute AS a
    JOIN pg_class AS c ON c.oid = a.attrelid
    LEFT JOIN pg_attrdef AS d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
    WHERE c.relnamespace = current_schema()::regnamespace
      AND c.relkind IN ('r', 'p') AND c.relname <> 'alembic_version'
      AND a.attnum > 0 AND NOT a.attisdropped
    UNION ALL
    SELECT 'index', indexname, indexdef FROM pg_indexes
    WHERE schemaname = current_schema() AND tablename <> 'alembic_version'
    UNION ALL
    SELECT 'constraint', c.relname || '.' || con.conname,
           pg_get_constraintdef(con.oid)
    FROM pg_constraint AS con
    JOIN pg_class AS c ON c.oid = con.conrelid
    WHERE con.connamespace = current_schema()::regnamespace
      AND c.relname <> 'alembic_version'
    UNION ALL
    SELECT 'enum', t.typname, string_agg(e.enumlabel, ', ' ORDER BY e.enumlabel)
    FROM pg_type AS t
    JOIN pg_enum AS e ON e.enumtypid = t.oid
    WHERE t.typnamespace = current_schema()::regnamespace
    GROUP BY t.typname
    """
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


async def _drop_database(db_name: str) -> None:
    await _admin_execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname = '{db_name}' AND pid <> pg_backend_pid()"
    )
    await _admin_execute(f'DROP DATABASE IF EXISTS "{db_name}"')


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


async def _build_with_create_all(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as connection:
            await connection.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await connection.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def migrated_database_url() -> AsyncIterator[str]:
    db_name = f"roboco_migrated_{os.getpid()}_{uuid4().hex[:8]}"
    await _admin_execute(f'CREATE DATABASE "{db_name}"')
    try:
        await asyncio.to_thread(_migrate_to_head, db_name)
        yield _build_url(db_name)
    finally:
        await _drop_database(db_name)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def create_all_database_url() -> AsyncIterator[str]:
    db_name = f"roboco_create_all_{os.getpid()}_{uuid4().hex[:8]}"
    await _admin_execute(f'CREATE DATABASE "{db_name}"')
    try:
        await _build_with_create_all(_build_url(db_name))
        yield _build_url(db_name)
    finally:
        await _drop_database(db_name)


def _autogenerate_differences(connection: Connection) -> list[Any]:
    context = MigrationContext.configure(
        connection, opts={"compare_type": True, "compare_server_default": True}
    )
    return list(compare_metadata(context, Base.metadata))


def _describe(difference: Any) -> str:
    """One readable line per autogenerate operation (a column-level change
    arrives as a list of tuples)."""
    if isinstance(difference, list):
        return "; ".join(_describe(item) for item in difference)
    operation = difference[0]
    if operation.startswith("modify_"):
        _, _, table, column, _, database_value, model_value = difference
        return (
            f"{operation} {table}.{column}: database {database_value!r}, "
            f"model {model_value!r}"
        )
    return f"{operation} {difference[1]!r}"


async def _catalog(url: str) -> dict[tuple[str, str], str]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            rows = await connection.execute(_CATALOG)
            return {(kind, key): definition for kind, key, definition in rows}
    finally:
        await engine.dispose()


def _catalog_differences(
    migrated: dict[tuple[str, str], str], create_all: dict[tuple[str, str], str]
) -> list[str]:
    lines = []
    for kind, key in sorted(migrated.keys() | create_all.keys()):
        in_migrated = migrated.get((kind, key))
        in_create_all = create_all.get((kind, key))
        if in_migrated != in_create_all:
            lines.append(
                f"{kind} {key}\n"
                f"  migrations: {in_migrated}\n"
                f"  create_all: {in_create_all}"
            )
    return lines


async def test_a_migrated_database_matches_the_models(
    migrated_database_url: str,
) -> None:
    engine = create_async_engine(migrated_database_url)
    try:
        async with engine.connect() as connection:
            differences = await connection.run_sync(_autogenerate_differences)
    finally:
        await engine.dispose()

    assert not differences, (
        "alembic upgrade head and roboco/db/tables.py disagree "
        "(remove_* = only the database has it, add_* = only the model has it):\n"
        + "\n".join(_describe(difference) for difference in differences)
    )


async def test_create_all_builds_the_schema_the_migrations_build(
    migrated_database_url: str, create_all_database_url: str
) -> None:
    migrated = await _catalog(migrated_database_url)
    create_all = await _catalog(create_all_database_url)

    differences = _catalog_differences(migrated, create_all)

    assert not differences, (
        "the schema alembic upgrade head builds and the one create_all builds "
        "differ:\n" + "\n".join(differences)
    )
