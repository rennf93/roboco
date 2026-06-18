"""Idempotently seed the Grok (xAI) provider row.

The ``modelprovider`` enum carries ``'grok'`` as of migration 038. This
migration seeds the corresponding ``provider_configs`` row so the Settings UI
can store the xAI key without an extra provisioning call.

The row starts disabled with the public xAI base URL and no key — the operator
sets the key via PUT /api/providers/grok/key, which encrypts it and enables the
provider. ON CONFLICT (name) DO NOTHING keeps this safe to re-run.

Revision ID: 039_seed_grok_provider
Revises: 038_modelprovider_grok
Create Date: 2026-06-18
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "039_seed_grok_provider"
down_revision = "038_modelprovider_grok"
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
                    'Grok (xAI)',
                    'grok',
                    'https://api.x.ai/v1',
                    NULL,
                    false,
                    now()
                )
            ON CONFLICT (name) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    # Drop model_assignments pointing at the Grok row first to avoid a FK
    # RESTRICT violation on provider_configs.id.
    op.execute(
        sa.text(
            "DELETE FROM model_assignments "
            "WHERE provider_config_id IN ("
            "    SELECT id FROM provider_configs WHERE name = 'Grok (xAI)'"
            ")"
        )
    )
    op.execute(sa.text("DELETE FROM provider_configs WHERE name = 'Grok (xAI)'"))
