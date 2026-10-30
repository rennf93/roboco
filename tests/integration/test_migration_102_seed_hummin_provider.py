"""Migration 102 tests — seed_hummin_provider.

Verifies the post-upgrade state and exercises the downgrade SQL ordering,
mirroring ``test_migration_091_seed_kimi_provider.py``'s own shape (hummin's
row is seeded ``enabled=true`` directly — spawn-time preflight + park is the
key gate, not a disabled row).

NOT a real alembic round-trip — the suite builds the test DB via
Base.metadata.create_all (see conftest). Migration 102's upgrade()/downgrade()
bodies are reviewed here; the tests guard the resulting DB-level contract —
in particular ``enabled=True`` at seed time and NULL base_url/auth_token (the
GLM Coding Plan endpoint is internal to the hummin CLI; the key arrives via
PUT /providers/hummin-key).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

_INSERT_SQL = text(
    """
    INSERT INTO provider_configs
        (id, name, type, base_url, auth_token_encrypted, enabled, created_at)
    VALUES
        (
            gen_random_uuid(),
            'hummin',
            'hummin',
            NULL,
            NULL,
            true,
            now()
        )
    ON CONFLICT (name) DO NOTHING
    """
)


@pytest.mark.asyncio
async def test_migration_102_upgrade_insert_contract(
    db_session: AsyncSession,
) -> None:
    """The upgrade INSERT SQL seeds the hummin row ENABLED with no stored
    secret and no base_url, and is idempotent."""
    # --- First run: the row should be inserted.
    await db_session.execute(_INSERT_SQL)
    await db_session.flush()

    result = await db_session.execute(
        text(
            "SELECT name, type, enabled, base_url, auth_token_encrypted "
            "FROM provider_configs "
            "WHERE name = 'hummin'"
        )
    )
    rows = list(result)
    assert len(rows) == 1
    name, ptype, enabled, base_url, auth_token = rows[0]
    assert name == "hummin"
    assert ptype == "hummin"
    # The load-bearing assertion: enabled=True at seed time (a missing key
    # is caught at spawn by the auth preflight + park, not by a disabled row).
    assert enabled is True
    assert base_url is None
    assert auth_token is None

    # --- Second run: ON CONFLICT DO NOTHING must not create a duplicate.
    await db_session.execute(_INSERT_SQL)
    await db_session.flush()

    result = await db_session.execute(
        text("SELECT id FROM provider_configs WHERE name = 'hummin'")
    )
    assert len(list(result)) == 1, (
        "Expected exactly one 'hummin' row after two INSERT executions; "
        "ON CONFLICT DO NOTHING must prevent duplicates."
    )


@pytest.mark.asyncio
async def test_migration_102_downgrade_clears_assignments_first(
    db_session: AsyncSession,
) -> None:
    """The downgrade deletes model_assignments pointing at the hummin row
    before deleting the row itself (FK RESTRICT on provider_configs.id)."""
    await db_session.execute(_INSERT_SQL)
    await db_session.flush()

    row_result = await db_session.execute(
        text("SELECT id FROM provider_configs WHERE name = 'hummin'")
    )
    row_id = row_result.scalar_one()

    # Downgrade body, in order.
    await db_session.execute(
        text(
            "DELETE FROM model_assignments "
            "WHERE provider_config_id IN ("
            "    SELECT id FROM provider_configs WHERE name = 'hummin'"
            ")"
        )
    )
    await db_session.execute(text("DELETE FROM provider_configs WHERE name = 'hummin'"))
    await db_session.flush()

    remaining_result = await db_session.execute(
        text("SELECT count(*) FROM provider_configs WHERE name = 'hummin'")
    )
    remaining = remaining_result.scalar_one()
    assert remaining == 0
    assert row_id is not None
