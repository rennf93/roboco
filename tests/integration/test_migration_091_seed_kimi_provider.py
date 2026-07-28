"""Migration 091 tests — seed_kimi_provider.

Verifies the post-upgrade state and exercises the downgrade SQL ordering,
mirroring ``test_migration_083_seed_openai_provider.py``'s own shape (Kimi's
row is seeded ``enabled=true`` directly, the same posture as Codex's — there
is no ``apply_mode="kimi"``-only gate it needs to wait behind, unlike GROK's
disabled-until-key-set seed).

NOT a real alembic round-trip — the suite builds the test DB via
Base.metadata.create_all (see conftest). Migration 091's upgrade()/downgrade()
bodies are reviewed here; the tests guard the resulting DB-level contract —
in particular ``enabled=True`` at seed time and NULL base_url/auth_token
(subscription auth, no stored secret).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from roboco.db.tables import ModelAssignmentTable, ProviderConfigTable
from roboco.models.base import AssignmentScope, ModelProvider
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
            'Kimi (Moonshot)',
            'kimi',
            NULL,
            NULL,
            true,
            now()
        )
    ON CONFLICT (name) DO NOTHING
    """
)


@pytest.mark.asyncio
async def test_migration_091_upgrade_insert_contract(
    db_session: AsyncSession,
) -> None:
    """The upgrade INSERT SQL seeds the Kimi row ENABLED with no stored
    secret (subscription auth), and is idempotent."""
    # --- First run: the row should be inserted.
    await db_session.execute(_INSERT_SQL)
    await db_session.flush()

    result = await db_session.execute(
        text(
            "SELECT name, type, enabled, base_url, auth_token_encrypted "
            "FROM provider_configs "
            "WHERE name = 'Kimi (Moonshot)'"
        )
    )
    rows = list(result)
    assert len(rows) == 1
    name, ptype, enabled, base_url, auth_token = rows[0]
    assert name == "Kimi (Moonshot)"
    assert ptype == "kimi"
    # The load-bearing assertion: enabled=True at seed time (parity with
    # Codex — there's no key-collection step gating this row).
    assert enabled is True
    assert base_url is None
    assert auth_token is None

    # --- Second run: ON CONFLICT DO NOTHING must not create a duplicate.
    await db_session.execute(_INSERT_SQL)
    await db_session.flush()

    result = await db_session.execute(
        text("SELECT id FROM provider_configs WHERE name = 'Kimi (Moonshot)'")
    )
    assert len(list(result)) == 1, (
        "Expected exactly one 'Kimi (Moonshot)' row after two INSERT "
        "executions; ON CONFLICT DO NOTHING must prevent duplicates."
    )


@pytest.mark.asyncio
async def test_migration_091_downgrade_deletes_assignments_before_config(
    db_session: AsyncSession,
) -> None:
    """Downgrade SQL deletes model_assignments before provider_configs.

    A FK RESTRICT constraint on model_assignments.provider_config_id means
    deleting provider_configs first would raise an IntegrityError.
    """
    suffix = uuid4().hex[:8]
    kimi = ProviderConfigTable(
        name=f"Kimi (Moonshot)-test-{suffix}",
        type=ModelProvider.KIMI,
        enabled=True,
    )
    db_session.add(kimi)
    await db_session.flush()

    assignment = ModelAssignmentTable(
        scope=AssignmentScope.AGENT_SLUG,
        scope_value=f"test-agent-{suffix}",
        provider_config_id=kimi.id,
        model_name="kimi-code/k3",
    )
    db_session.add(assignment)
    await db_session.flush()

    result = await db_session.execute(
        text("SELECT id FROM provider_configs WHERE name = :name").bindparams(
            name=kimi.name
        )
    )
    assert result.scalar_one_or_none() is not None

    result = await db_session.execute(
        text("SELECT id FROM model_assignments WHERE scope_value = :sv").bindparams(
            sv=assignment.scope_value
        )
    )
    assert result.scalar_one_or_none() is not None

    # Step 1: delete referencing model_assignments first.
    await db_session.execute(
        text(
            "DELETE FROM model_assignments "
            "WHERE provider_config_id IN ("
            "    SELECT id FROM provider_configs WHERE name = :name"
            ")"
        ).bindparams(name=kimi.name)
    )
    # Step 2: now safe to delete the provider row.
    await db_session.execute(
        text("DELETE FROM provider_configs WHERE name = :name").bindparams(
            name=kimi.name
        )
    )

    result = await db_session.execute(
        text("SELECT id FROM provider_configs WHERE name = :name").bindparams(
            name=kimi.name
        )
    )
    assert result.scalar_one_or_none() is None, (
        "provider_configs row should be deleted by downgrade"
    )

    result = await db_session.execute(
        text("SELECT id FROM model_assignments WHERE scope_value = :sv").bindparams(
            sv=assignment.scope_value
        )
    )
    assert result.scalar_one_or_none() is None, (
        "model_assignments row should be deleted before provider_configs"
    )
