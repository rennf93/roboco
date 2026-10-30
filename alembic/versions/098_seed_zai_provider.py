"""Idempotently seed the Z.ai provider row.

The ``modelprovider`` enum carries ``'zai'`` as of migration 097.
This migration seeds the corresponding ``provider_configs`` row so the
Settings UI can list it for role/agent model assignment.

Unlike the Anthropic-default path (``base_url = NULL`` → mounted
``~/.claude`` credentials, no injection), Z.ai is routed by
``ANTHROPIC_BASE_URL`` injection at spawn, so the row carries Z.ai's
Anthropic-compatible endpoint in ``base_url`` from birth. The row is
seeded ``enabled=false`` with a NULL token — it stays disabled until the
operator sets a key via PUT /providers/zai-key, which Fernet-encrypts +
enables in the same transaction (mirroring the OLLAMA_CLOUD pattern).
ON CONFLICT (name) DO NOTHING keeps this safe to re-run.

Revision ID: 098_seed_zai_provider
Revises: 097_modelprovider_zai
Create Date: 2026-09-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "098_seed_zai_provider"
down_revision = "097_modelprovider_zai"
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
    )


def downgrade() -> None:
    # Drop model_assignments pointing at the Z.ai row first to avoid a
    # FK RESTRICT violation on provider_configs.id.
    op.execute(
        sa.text(
            "DELETE FROM model_assignments "
            "WHERE provider_config_id IN ("
            "    SELECT id FROM provider_configs WHERE name = 'Z.ai'"
            ")"
        )
    )
    op.execute(sa.text("DELETE FROM provider_configs WHERE name = 'Z.ai'"))
