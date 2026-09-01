"""Idempotently seed the OpenRouter provider row.

The ``modelprovider`` enum carries ``'openrouter'`` as of migration 095.
This migration seeds the corresponding ``provider_configs`` row so the
Settings UI can list it for role/agent model assignment.

Seeded ``enabled=false`` — unlike the subscription-CLI providers (Codex,
Gemini, Kimi) which have no key to gate on, OpenRouter authenticates with
a metered API key stored Fernet-encrypted on the provider row. The row
stays disabled until the operator sets a key via PUT /providers/openrouter-
key, which encrypts + enables in the same transaction (mirroring the GROK
pattern). ``base_url``/``auth_token_encrypted`` stay NULL until then.
ON CONFLICT (name) DO NOTHING keeps this safe to re-run.

Revision ID: 096_seed_openrouter_provider
Revises: 095_modelprovider_openrouter
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "096_seed_openrouter_provider"
down_revision = "095_modelprovider_openrouter"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            """
            INSERT INTO provider_configs
                (id, name, type, base_url, auth_token_encrypted, enabled, created_at)
            VALUES
                (
                    gen_random_uuid(),
                    'OpenRouter',
                    'openrouter',
                    NULL,
                    NULL,
                    false,
                    now()
                )
            ON CONFLICT (name) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    # Drop model_assignments pointing at the OpenRouter row first to avoid a
    # FK RESTRICT violation on provider_configs.id.
    op.execute(
        sa.text(
            "DELETE FROM model_assignments "
            "WHERE provider_config_id IN ("
            "    SELECT id FROM provider_configs WHERE name = 'OpenRouter'"
            ")"
        )
    )
    op.execute(sa.text("DELETE FROM provider_configs WHERE name = 'OpenRouter'"))
