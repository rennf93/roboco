"""Migration 097/098 tests: modelprovider_nebius + seed_nebius_provider.

Verifies the post-upgrade state and exercises the downgrade SQL ordering,
mirroring ``test_migration_096_seed_openrouter_provider.py``'s own shape with
the same posture: Nebius's row is seeded ``enabled=false`` (the GROK
posture, not Kimi's). Unlike the subscription-CLI providers there IS a
key-collection step gating the row: the Fernet-encrypted Nebius API key
set via PUT /providers/nebius-key, which encrypts + enables in the same
transaction.

NOT a real alembic round-trip: the suite builds the test DB via
Base.metadata.create_all (see conftest). Migrations 097/098's
upgrade()/downgrade() bodies are reviewed here; the tests guard the resulting
DB-level contract, in particular ``enabled=False`` at seed time and NULL
base_url/auth_token (no stored secret until the operator sets a key). The
097 enum change is exercised as a real round-trip against the
``modelprovider`` PG enum type.
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

# The exact INSERT from migration 098's upgrade() (seed enabled=false).
_INSERT_SQL = text(
    """
    INSERT INTO provider_configs
        (id, name, type, base_url, auth_token_encrypted, enabled, created_at)
    VALUES
        (
            gen_random_uuid(),
            'Nebius',
            'nebius',
            NULL,
            NULL,
            false,
            now()
        )
    ON CONFLICT (name) DO NOTHING
    """
)


@pytest.mark.asyncio
async def test_migration_098_upgrade_insert_contract(
    db_session: AsyncSession,
) -> None:
    """The upgrade INSERT SQL seeds the Nebius row DISABLED with no stored
    secret (the key arrives later via the encrypted-key endpoint), and is
    idempotent."""
    # --- First run: the row should be inserted.
    await db_session.execute(_INSERT_SQL)
    await db_session.flush()

    result = await db_session.execute(
        text(
            "SELECT name, type, enabled, base_url, auth_token_encrypted "
            "FROM provider_configs "
            "WHERE name = 'Nebius'"
        )
    )
    rows = list(result)
    assert len(rows) == 1
    name, ptype, enabled, base_url, auth_token = rows[0]
    assert name == "Nebius"
    assert ptype == "nebius"
    # The load-bearing assertion: enabled=False at seed time (the GROK
    # posture: there is a key-collection step gating this row, unlike
    # Kimi/Codex).
    assert enabled is False
    assert base_url is None
    assert auth_token is None

    # --- Second run: ON CONFLICT DO NOTHING must not create a duplicate.
    await db_session.execute(_INSERT_SQL)
    await db_session.flush()

    result = await db_session.execute(
        text("SELECT id FROM provider_configs WHERE name = 'Nebius'")
    )
    assert len(list(result)) == 1, (
        "Expected exactly one 'Nebius' row after two INSERT "
        "executions; ON CONFLICT DO NOTHING must prevent duplicates."
    )


@pytest.mark.asyncio
async def test_migration_098_downgrade_deletes_assignments_before_config(
    db_session: AsyncSession,
) -> None:
    """Downgrade SQL deletes model_assignments before provider_configs.

    A FK RESTRICT constraint on model_assignments.provider_config_id means
    deleting provider_configs first would raise an IntegrityError.
    """
    suffix = uuid4().hex[:8]
    nebius = ProviderConfigTable(
        name=f"Nebius-test-{suffix}",
        type=ModelProvider.NEBIUS,
        enabled=False,
    )
    db_session.add(nebius)
    await db_session.flush()

    assignment = ModelAssignmentTable(
        scope=AssignmentScope.AGENT_SLUG,
        scope_value=f"test-agent-{suffix}",
        provider_config_id=nebius.id,
        model_name="nvidia/nemotron-3-super-120b",
    )
    db_session.add(assignment)
    await db_session.flush()

    result = await db_session.execute(
        text("SELECT id FROM provider_configs WHERE name = :name").bindparams(
            name=nebius.name
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
        ).bindparams(name=nebius.name)
    )
    # Step 2: now safe to delete the provider row.
    await db_session.execute(
        text("DELETE FROM provider_configs WHERE name = :name").bindparams(
            name=nebius.name
        )
    )

    result = await db_session.execute(
        text("SELECT id FROM provider_configs WHERE name = :name").bindparams(
            name=nebius.name
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


@pytest.mark.asyncio
async def test_migration_097_nebius_enum_round_trip(
    db_session: AsyncSession,
) -> None:
    """The ``modelprovider`` PG enum carries ``nebius`` (migration 097)
    and the value round-trips through the ORM."""
    # Raw PG-level check: the enum type itself contains the label.
    result = await db_session.execute(text("SELECT 'nebius'::modelprovider = 'nebius'"))
    assert result.scalar_one() is True

    # ORM round-trip: a row typed NEBIUS survives write + read.
    suffix = uuid4().hex[:8]
    nebius = ProviderConfigTable(
        name=f"Nebius-enum-{suffix}",
        type=ModelProvider.NEBIUS,
        enabled=False,
    )
    db_session.add(nebius)
    await db_session.flush()

    reloaded = await db_session.get(ProviderConfigTable, nebius.id)
    assert reloaded is not None
    assert reloaded.type is ModelProvider.NEBIUS
