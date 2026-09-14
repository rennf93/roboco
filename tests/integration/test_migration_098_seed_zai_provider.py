"""Migration 097/098 tests: modelprovider_zai + seed_zai_provider.

Verifies the post-upgrade state, mirroring
``test_migration_096_seed_openrouter_provider.py``'s own shape. The Z.ai
row is seeded ``enabled=false`` (the GROK/OLLAMA_CLOUD posture: a
key-collection step gates the row — PUT /providers/zai-key encrypts +
enables in the same transaction) with ONE decisive contrast to
OpenRouter: ``base_url`` is NOT NULL. Z.ai routes via ANTHROPIC_BASE_URL
injection at spawn (the OLLAMA_CLOUD shape), so the row carries Z.ai's
Anthropic-compatible endpoint from birth.

NOT a real alembic round-trip: the suite builds the test DB via
Base.metadata.create_all (see conftest). Migrations 097/098's
upgrade()/downgrade() bodies are reviewed here; the tests guard the
resulting DB-level contract.
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

# The exact INSERT from migration 098's upgrade() (seed enabled=false,
# base_url carrying Z.ai's Anthropic-compatible endpoint).
_INSERT_SQL = text(
    """
    INSERT INTO provider_configs
        (id, name, type, base_url, auth_token_encrypted, enabled, created_at)
    VALUES
        (
            gen_random_uuid(),
            'Z.ai',
            'zai',
            'https://api.z.ai/api/anthropic',
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
    """The upgrade INSERT SQL seeds the Z.ai row DISABLED, with the
    Anthropic-compatible endpoint already in base_url and no stored secret,
    and is idempotent."""
    await db_session.execute(_INSERT_SQL)
    await db_session.flush()

    result = await db_session.execute(
        text(
            "SELECT name, type, enabled, base_url, auth_token_encrypted "
            "FROM provider_configs "
            "WHERE name = 'Z.ai'"
        )
    )
    rows = list(result)
    assert len(rows) == 1
    name, ptype, enabled, base_url, auth_token = rows[0]
    assert name == "Z.ai"
    assert ptype == "zai"
    # The load-bearing assertion: enabled=False at seed time (the
    # OLLAMA_CLOUD posture: there is a key-collection step gating this row).
    assert enabled is False
    # The decisive contrast to OpenRouter: the injection endpoint ships
    # with the row, because ANTHROPIC_BASE_URL injection is the whole
    # routing mechanism for this provider.
    assert base_url == "https://api.z.ai/api/anthropic"
    assert auth_token is None

    # Second run: ON CONFLICT DO NOTHING must not create a duplicate.
    await db_session.execute(_INSERT_SQL)
    await db_session.flush()

    result = await db_session.execute(
        text("SELECT id FROM provider_configs WHERE name = 'Z.ai'")
    )
    assert len(list(result)) == 1, (
        "Expected exactly one 'Z.ai' row after two INSERT executions; "
        "ON CONFLICT DO NOTHING must prevent duplicates."
    )


@pytest.mark.asyncio
async def test_modelprovider_enum_accepts_zai(
    db_session: AsyncSession,
) -> None:
    """The modelprovider PG enum accepts 'zai' and round-trips through the
    ORM as ModelProvider.ZAI (mirrors the 095 enum round-trip)."""
    suffix = uuid4().hex[:8]
    row = ProviderConfigTable(
        name=f"Z.ai-test-{suffix}",
        type=ModelProvider.ZAI,
        base_url="https://api.z.ai/api/anthropic",
        enabled=False,
    )
    db_session.add(row)
    await db_session.flush()

    result = await db_session.execute(
        text("SELECT type FROM provider_configs WHERE name = :name").bindparams(
            name=row.name
        )
    )
    assert result.scalar_one() == "zai"

    await db_session.refresh(row)
    assert row.type is ModelProvider.ZAI


@pytest.mark.asyncio
async def test_migration_098_downgrade_deletes_assignments_before_config(
    db_session: AsyncSession,
) -> None:
    """Downgrade SQL deletes model_assignments before provider_configs.

    A FK RESTRICT constraint on model_assignments.provider_config_id means
    deleting provider_configs first would raise an IntegrityError.
    """
    suffix = uuid4().hex[:8]
    zai = ProviderConfigTable(
        name=f"Z.ai-test-{suffix}",
        type=ModelProvider.ZAI,
        enabled=False,
    )
    db_session.add(zai)
    await db_session.flush()

    assignment = ModelAssignmentTable(
        scope=AssignmentScope.AGENT_SLUG,
        scope_value=f"test-agent-{suffix}",
        provider_config_id=zai.id,
        model_name="glm-5.3-flash",
    )
    db_session.add(assignment)
    await db_session.flush()

    result = await db_session.execute(
        text("SELECT id FROM provider_configs WHERE name = :name").bindparams(
            name=zai.name
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
        ).bindparams(name=zai.name)
    )
    await db_session.flush()

    # Step 2: deleting the provider row now succeeds (no RESTRICT hit).
    await db_session.execute(
        text("DELETE FROM provider_configs WHERE name = :name").bindparams(
            name=zai.name
        )
    )
    await db_session.flush()

    result = await db_session.execute(
        text("SELECT id FROM provider_configs WHERE name = :name").bindparams(
            name=zai.name
        )
    )
    assert result.scalar_one_or_none() is None
