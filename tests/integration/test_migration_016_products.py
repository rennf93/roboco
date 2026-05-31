from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_products_tables_and_task_fk_exist(db_session: AsyncSession) -> None:
    """Post-state assertion (create_all schema, mirrors test_migration_013/014).

    NOT a real alembic round-trip — the suite builds the test DB via
    Base.metadata.create_all. Migration 016's upgrade()/downgrade() bodies are
    reviewed by hand; this test guards the resulting table/column shape.
    """
    tables = {
        r[0]
        for r in await db_session.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_name IN ('products', 'product_projects')"
            )
        )
    }
    assert tables == {"products", "product_projects"}

    cols = {
        r[0]
        for r in await db_session.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = 'tasks' AND column_name = 'product_id'"
            )
        )
    }
    assert "product_id" in cols

    fk = {
        r[0]
        for r in await db_session.execute(
            text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'tasks'::regclass AND contype = 'f' "
                "AND conname = 'fk_tasks_product_id_products'"
            )
        )
    }
    assert "fk_tasks_product_id_products" in fk
