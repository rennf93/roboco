"""Idempotently seed the Kimi (Moonshot) provider row.

The ``modelprovider`` enum carries ``'kimi'`` as of migration 090. This
migration seeds the corresponding ``provider_configs`` row so the Settings UI
can list it for role/agent model assignment.

Like Codex (migration 083), seeded ``enabled=true`` directly: the
KimiCliProvider authenticates from a mounted subscription credential
(``~/.kimi-code/credentials/kimi-code.json``, from a `kimi login` device-code
flow), never a stored API key, so there is no secret to withhold behind a
disabled row — ``base_url``/``auth_token_encrypted`` stay NULL permanently
(mirroring Gemini's row). Unlike Gemini's row (seeded disabled, flipped by a
follow-up migration), there is no reason to gate this one behind a second
migration since there's no key-collection step it needs to wait on.
ON CONFLICT (name) DO NOTHING keeps this safe to re-run.

Revision ID: 091_seed_kimi_provider
Revises: 090_modelprovider_kimi
Create Date: 2026-07-28
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "091_seed_kimi_provider"
down_revision = "090_modelprovider_kimi"
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
                    'Kimi (Moonshot)',
                    'kimi',
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
    # Drop model_assignments pointing at the Kimi row first to avoid a FK
    # RESTRICT violation on provider_configs.id.
    op.execute(
        sa.text(
            "DELETE FROM model_assignments "
            "WHERE provider_config_id IN ("
            "    SELECT id FROM provider_configs WHERE name = 'Kimi (Moonshot)'"
            ")"
        )
    )
    op.execute(sa.text("DELETE FROM provider_configs WHERE name = 'Kimi (Moonshot)'"))
