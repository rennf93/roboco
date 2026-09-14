"""Idempotently seed the Nebius provider row.

The ``modelprovider`` enum carries ``'nebius'`` as of migration 097.
This migration seeds the corresponding ``provider_configs`` row so the
Settings UI can list it for role/agent model assignment.

Seeded ``enabled=false`` — unlike the subscription-CLI providers (Codex,
Gemini, Kimi) which have no key to gate on, Nebius Token Factory
authenticates with a metered API key stored Fernet-encrypted on the
provider row. The row stays disabled until the operator sets a key via
PUT /providers/nebius-key, which encrypts + enables in the same
transaction (mirroring the GROK pattern). ``base_url``/
``auth_token_encrypted`` stay NULL until then.
ON CONFLICT (name) DO NOTHING keeps this safe to re-run.

Revision ID: 100_seed_nebius_provider
Revises: 099_modelprovider_nebius
Create Date: 2026-09-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "100_seed_nebius_provider"
down_revision = "099_modelprovider_nebius"
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
    )


def downgrade() -> None:
    # Drop model_assignments pointing at the Nebius row first to avoid a
    # FK RESTRICT violation on provider_configs.id.
    op.execute(
        sa.text(
            "DELETE FROM model_assignments "
            "WHERE provider_config_id IN ("
            "    SELECT id FROM provider_configs WHERE name = 'Nebius'"
            ")"
        )
    )
    op.execute(sa.text("DELETE FROM provider_configs WHERE name = 'Nebius'"))
