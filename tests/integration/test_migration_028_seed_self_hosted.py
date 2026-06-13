"""Migration 028 tests — seed_self_hosted_provider.

Verifies the post-upgrade state and exercises the downgrade SQL ordering
to prove the FK-safe delete sequence works.

NOT a real alembic round-trip — the suite builds the test DB via
Base.metadata.create_all (see conftest). Migration 028's upgrade()/downgrade()
bodies are reviewed here; the tests guard the resulting DB-level contract.
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


@pytest.mark.asyncio
async def test_migration_028_upgrade_insert_contract(
    db_session: AsyncSession,
) -> None:
    """The upgrade INSERT SQL seeds the correct LOCAL provider row and is idempotent.

    Executes the INSERT ... ON CONFLICT DO NOTHING SQL from migration 028's
    upgrade() directly in the test session, verifying:
      - name='Self-Hosted (Ollama)', type='local', enabled=False on the row.
      - Running the same INSERT a second time leaves exactly one row (idempotency).
    """
    _insert_sql = text(
        """
        INSERT INTO provider_configs
            (id, name, type, base_url, auth_token_encrypted, enabled, created_at)
        VALUES
            (
                gen_random_uuid(),
                'Self-Hosted (Ollama)',
                'local',
                NULL,
                NULL,
                false,
                now()
            )
        ON CONFLICT (name) DO NOTHING
        """
    )

    # --- First run: the row should be inserted.
    await db_session.execute(_insert_sql)
    await db_session.flush()

    # Verify the field contract on the newly-inserted row.
    result = await db_session.execute(
        text(
            "SELECT name, type, enabled "
            "FROM provider_configs "
            "WHERE name = 'Self-Hosted (Ollama)'"
        )
    )
    rows = list(result)
    assert len(rows) == 1
    name, ptype, enabled = rows[0]
    assert name == "Self-Hosted (Ollama)"
    assert ptype == "local"
    assert enabled is False  # starts disabled; user configures via PUT /self-hosted

    # --- Second run: ON CONFLICT DO NOTHING must not create a duplicate.
    await db_session.execute(_insert_sql)
    await db_session.flush()

    result = await db_session.execute(
        text("SELECT id FROM provider_configs WHERE name = 'Self-Hosted (Ollama)'")
    )
    assert len(list(result)) == 1, (
        "Expected exactly one 'Self-Hosted (Ollama)' row after two INSERT "
        "executions; ON CONFLICT DO NOTHING must prevent duplicates."
    )


@pytest.mark.asyncio
async def test_migration_028_downgrade_deletes_assignments_before_config(
    db_session: AsyncSession,
) -> None:
    """Downgrade SQL deletes model_assignments before provider_configs.

    Simulates the downgrade() logic from migration 028:
      1. DELETE FROM model_assignments WHERE provider_config_id IN (SELECT id ...)
      2. DELETE FROM provider_configs WHERE name = 'Self-Hosted (Ollama)'

    A FK RESTRICT constraint on model_assignments.provider_config_id means that
    executing step 2 before step 1 would raise an IntegrityError. This test
    proves that doing them in the correct order succeeds without violation.
    """
    # --- Arrange: insert a fresh LOCAL provider row and a referencing assignment.
    suffix = uuid4().hex[:8]
    local = ProviderConfigTable(
        name=f"Self-Hosted (Ollama)-test-{suffix}",
        type=ModelProvider.LOCAL,
        enabled=False,
    )
    db_session.add(local)
    await db_session.flush()

    assignment = ModelAssignmentTable(
        scope=AssignmentScope.AGENT_SLUG,
        scope_value=f"test-agent-{suffix}",
        provider_config_id=local.id,
        model_name="llama3.1:8b",
    )
    db_session.add(assignment)
    await db_session.flush()

    # Verify both rows exist before we run the downgrade SQL.
    result = await db_session.execute(
        text("SELECT id FROM provider_configs WHERE name = :name").bindparams(
            name=local.name
        )
    )
    assert result.scalar_one_or_none() is not None

    result = await db_session.execute(
        text("SELECT id FROM model_assignments WHERE scope_value = :sv").bindparams(
            sv=assignment.scope_value
        )
    )
    assert result.scalar_one_or_none() is not None

    # --- Act: execute downgrade SQL in the correct FK-safe order.
    # Step 1: delete referencing model_assignments first.
    await db_session.execute(
        text(
            "DELETE FROM model_assignments "
            "WHERE provider_config_id IN ("
            "    SELECT id FROM provider_configs WHERE name = :name"
            ")"
        ).bindparams(name=local.name)
    )
    # Step 2: now safe to delete the provider row.
    await db_session.execute(
        text("DELETE FROM provider_configs WHERE name = :name").bindparams(
            name=local.name
        )
    )

    # --- Assert: both rows are gone, no IntegrityError was raised.
    result = await db_session.execute(
        text("SELECT id FROM provider_configs WHERE name = :name").bindparams(
            name=local.name
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
