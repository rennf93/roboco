"""Idempotently seed the Self-Hosted (Ollama) LOCAL provider row.

Revision ID: 028_seed_self_hosted_provider
Revises: 027_system_settings
Create Date: 2026-06-12

The `provider_configs` table already has `type='local'` in the
`modelprovider` enum (seeded in 004_provider_routing). This migration
seeds the corresponding row so the Settings UI can configure a
self-hosted Ollama server without requiring an additional API call.

The row starts disabled with no base_url — the user configures those
via PUT /api/providers/self-hosted. ON CONFLICT DO NOTHING makes this
migration safe to re-run on a DB that already has the row (e.g. from
a prior manual insert or a future fresh-schema bootstrap).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "028_seed_self_hosted_provider"
down_revision = "027_system_settings"
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
    )


def downgrade() -> None:
    # Delete any model_assignments that point to the LOCAL provider row first
    # to avoid a FK RESTRICT violation on provider_configs.id.
    op.execute(
        sa.text(
            "DELETE FROM model_assignments "
            "WHERE provider_config_id IN ("
            "    SELECT id FROM provider_configs WHERE name = 'Self-Hosted (Ollama)'"
            ")"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM provider_configs "
            "WHERE name = 'Self-Hosted (Ollama)'"
        )
    )
