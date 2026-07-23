"""Migration 083 tests — seed_openai_provider.

Verifies the post-upgrade state and exercises the downgrade SQL ordering,
mirroring ``test_migration_028_seed_self_hosted.py`` and
``039_seed_grok_provider``'s own shape.

NOT a real alembic round-trip — the suite builds the test DB via
Base.metadata.create_all (see conftest). Migration 083's upgrade()/downgrade()
bodies are reviewed here; the tests guard the resulting DB-level contract —
in particular ``enabled=True`` at seed time, the one detail that diverges
from GROK's own seed (see the migration's docstring for why: there is no
``apply_mode="codex"`` write path to flip it later).
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
            'Codex (OpenAI)',
            'openai',
            'https://api.openai.com/v1',
            NULL,
            true,
            now()
        )
    ON CONFLICT (name) DO NOTHING
    """
)


@pytest.mark.asyncio
async def test_migration_083_upgrade_insert_contract(
    db_session: AsyncSession,
) -> None:
    """The upgrade INSERT SQL seeds the Codex row ENABLED (unlike GROK's
    seed, which starts disabled) and is idempotent."""
    # --- First run: the row should be inserted.
    await db_session.execute(_INSERT_SQL)
    await db_session.flush()

    result = await db_session.execute(
        text(
            "SELECT name, type, enabled, base_url "
            "FROM provider_configs "
            "WHERE name = 'Codex (OpenAI)'"
        )
    )
    rows = list(result)
    assert len(rows) == 1
    name, ptype, enabled, base_url = rows[0]
    assert name == "Codex (OpenAI)"
    assert ptype == "openai"
    # The load-bearing assertion: enabled=True at seed time. Seeding False
    # (GROK's convention) would leave resolve_for_agent silently falling back
    # to Anthropic forever, since no apply_mode="codex" write path exists to
    # flip it — the exact "unreachable" failure this migration fixes.
    assert enabled is True
    assert base_url == "https://api.openai.com/v1"

    # --- Second run: ON CONFLICT DO NOTHING must not create a duplicate.
    await db_session.execute(_INSERT_SQL)
    await db_session.flush()

    result = await db_session.execute(
        text("SELECT id FROM provider_configs WHERE name = 'Codex (OpenAI)'")
    )
    assert len(list(result)) == 1, (
        "Expected exactly one 'Codex (OpenAI)' row after two INSERT "
        "executions; ON CONFLICT DO NOTHING must prevent duplicates."
    )


@pytest.mark.asyncio
async def test_migration_083_downgrade_deletes_assignments_before_config(
    db_session: AsyncSession,
) -> None:
    """Downgrade SQL deletes model_assignments before provider_configs.

    A FK RESTRICT constraint on model_assignments.provider_config_id means
    deleting provider_configs first would raise an IntegrityError.
    """
    suffix = uuid4().hex[:8]
    openai = ProviderConfigTable(
        name=f"Codex (OpenAI)-test-{suffix}",
        type=ModelProvider.OPENAI,
        enabled=True,
    )
    db_session.add(openai)
    await db_session.flush()

    assignment = ModelAssignmentTable(
        scope=AssignmentScope.AGENT_SLUG,
        scope_value=f"test-agent-{suffix}",
        provider_config_id=openai.id,
        model_name="gpt-5.3-codex",
    )
    db_session.add(assignment)
    await db_session.flush()

    result = await db_session.execute(
        text("SELECT id FROM provider_configs WHERE name = :name").bindparams(
            name=openai.name
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
        ).bindparams(name=openai.name)
    )
    # Step 2: now safe to delete the provider row.
    await db_session.execute(
        text("DELETE FROM provider_configs WHERE name = :name").bindparams(
            name=openai.name
        )
    )

    result = await db_session.execute(
        text("SELECT id FROM provider_configs WHERE name = :name").bindparams(
            name=openai.name
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
