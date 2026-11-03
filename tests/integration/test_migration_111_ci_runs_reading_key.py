"""Migration 111: the ci_runs reading key becomes NULLS NOT DISTINCT.

Runs the migration's own ``upgrade()`` / ``downgrade()`` through alembic's
``Operations`` on the test session's connection, after putting ``ci_runs``
back into 108's shape (a plain unique constraint) and seeding the NULL-
workflow duplicates that shape let in. Everything happens inside the test's
transaction, which the ``db_session`` teardown rolls back, so the suite's
schema is untouched.
"""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text

if TYPE_CHECKING:
    from types import ModuleType

    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import Session

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "111_ci_runs_reading_key.py"
)


def _load_migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("migration_111", _MIGRATION)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def _run(db_session: AsyncSession, step: str) -> None:
    migration = _load_migration()

    def _apply(session: Session) -> None:
        context = MigrationContext.configure(session.connection())
        with Operations.context(context):
            getattr(migration, step)()

    await db_session.run_sync(_apply)


async def _restore_108_key(db_session: AsyncSession) -> None:
    await db_session.execute(text("DROP INDEX ux_ci_runs_reading"))
    await db_session.execute(
        text(
            "ALTER TABLE ci_runs ADD CONSTRAINT ux_ci_runs_reading "
            "UNIQUE (project_slug, workflow, observed_at_str)"
        )
    )


async def _insert(
    db_session: AsyncSession,
    *,
    created_at: datetime,
    workflow: str | None,
    observed_at_str: str,
    project: str = "proj",
) -> str:
    row_id = str(uuid4())
    await db_session.execute(
        text(
            "INSERT INTO ci_runs (id, created_at, project_slug, workflow, "
            "conclusion, is_breach, observed_at_str) VALUES "
            "(:id, :created_at, :project, :workflow, 'failure', true, :stamp)"
        ),
        {
            "id": row_id,
            "created_at": created_at,
            "project": project,
            "workflow": workflow,
            "stamp": observed_at_str,
        },
    )
    return row_id


async def _key_definition(db_session: AsyncSession) -> dict[str, Any]:
    index = await db_session.execute(
        text(
            "SELECT indexdef FROM pg_indexes "
            "WHERE tablename = 'ci_runs' AND indexname = 'ux_ci_runs_reading'"
        )
    )
    constraint = await db_session.execute(
        text(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
            "WHERE conrelid = 'ci_runs'::regclass AND conname = 'ux_ci_runs_reading'"
        )
    )
    return {"index": index.scalar_one(), "constraint": constraint.scalar_one_or_none()}


@pytest.mark.asyncio
async def test_upgrade_keeps_each_readings_first_observation(
    db_session: AsyncSession,
) -> None:
    await _restore_108_key(db_session)
    now = datetime.now(UTC)
    first = await _insert(
        db_session,
        created_at=now - timedelta(hours=3),
        workflow=None,
        observed_at_str="run-1",
    )
    for hours_ago in (2, 1):
        await _insert(
            db_session,
            created_at=now - timedelta(hours=hours_ago),
            workflow=None,
            observed_at_str="run-1",
        )
    named = await _insert(
        db_session, created_at=now, workflow="ci.yml", observed_at_str="run-1"
    )
    other = await _insert(
        db_session, created_at=now, workflow=None, observed_at_str="run-2"
    )

    await _run(db_session, "upgrade")

    remaining = await db_session.execute(text("SELECT id::text FROM ci_runs"))
    assert set(remaining.scalars()) == {first, named, other}
    key = await _key_definition(db_session)
    assert key["constraint"] is None
    assert key["index"].startswith("CREATE UNIQUE INDEX ux_ci_runs_reading")
    assert key["index"].endswith("NULLS NOT DISTINCT")


@pytest.mark.asyncio
async def test_upgrade_replaces_a_create_all_built_key(
    db_session: AsyncSession,
) -> None:
    """The suite's own schema already has the key as a unique index (the
    model's shape); the upgrade replaces it without needing 108's constraint."""
    await db_session.execute(text("DROP INDEX ux_ci_runs_reading"))
    await db_session.execute(
        text(
            "CREATE UNIQUE INDEX ux_ci_runs_reading "
            "ON ci_runs (project_slug, workflow, observed_at_str)"
        )
    )

    await _run(db_session, "upgrade")

    key = await _key_definition(db_session)
    assert key["constraint"] is None
    assert key["index"].endswith("NULLS NOT DISTINCT")


@pytest.mark.asyncio
async def test_downgrade_restores_108s_unique_constraint(
    db_session: AsyncSession,
) -> None:
    await _run(db_session, "downgrade")

    key = await _key_definition(db_session)
    assert key["constraint"] == "UNIQUE (project_slug, workflow, observed_at_str)"
    assert not key["index"].endswith("NULLS NOT DISTINCT")
