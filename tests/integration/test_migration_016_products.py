from __future__ import annotations

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_products_tables_and_task_fk_exist(db_session) -> None:  # type: ignore[no-untyped-def]
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
