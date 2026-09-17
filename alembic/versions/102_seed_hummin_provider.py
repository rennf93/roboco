"""Idempotently seed the Hummin (GLM-native CLI) provider row.

The ``modelprovider`` enum carries ``'hummin'`` as of migration 101. This
migration seeds the corresponding ``provider_configs`` row so the Settings UI
can list it for role/agent model assignment.

``enabled=true`` from birth: unlike ZAI (whose spawn would route at a dead
endpoint without a key, hence gated), a hummin spawn without a stored key
still fails safe — the container's auth preflight exits 78 and the
orchestrator parks the provider until the operator sets the Z.ai key via
PUT /providers/hummin-key. ``base_url`` stays NULL permanently (the GLM
Coding Plan endpoint is internal to the hummin CLI) and
``auth_token_encrypted`` starts NULL (Fernet-encrypted by the key endpoint).
ON CONFLICT (name) DO NOTHING keeps this safe to re-run.

Revision ID: 102_seed_hummin_provider
Revises: 101_modelprovider_hummin
Create Date: 2026-09-17
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "102_seed_hummin_provider"
down_revision = "101_modelprovider_hummin"
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
                    'hummin',
                    'hummin',
                    NULL,
                    NULL,
                    true,
                    now()
                )
            ON CONFLICT (name) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    # Drop model_assignments pointing at the hummin row first to avoid a FK
    # RESTRICT violation on provider_configs.id.
    op.execute(
        sa.text(
            "DELETE FROM model_assignments "
            "WHERE provider_config_id IN ("
            "    SELECT id FROM provider_configs WHERE name = 'hummin'"
            ")"
        )
    )
    op.execute(sa.text("DELETE FROM provider_configs WHERE name = 'hummin'"))
