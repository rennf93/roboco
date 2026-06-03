"""Idempotently (re)seed the default model providers on existing databases.

Revision ID: 019_seed_default_providers
Revises: 018_task_project_id_nullable
Create Date: 2026-06-02

Migration 004 seeds the "Anthropic (default)" and "Ollama Cloud" provider rows,
but that body only executes when the chain is applied from base. A database
whose schema was originally built by `create_all` and then stamped forward to a
later revision has the `provider_configs` TABLE but NOT those seed rows — so the
Settings UI 404s on `GET`/`PUT /api/providers/ollama-key` ("Ollama Cloud
provider not seeded"). This forward migration re-seeds both rows so existing
databases get them on the next `alembic upgrade head`, while staying a no-op on
a fresh DB where 004 already inserted them (ON CONFLICT skips the duplicate).

Type labels are lowercase to match the ORM's `_str_enum` (StrEnum `.value`) and
the `modelprovider` enum produced by both `create_all` and the corrected 004.

`ON CONFLICT (name)` infers the arbiter from the unique index on
`provider_configs.name` (`ix_provider_configs_name`, made unique in 017). The
old `uq_provider_configs_name` CONSTRAINT was dropped in 017, so it must not be
named here.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "019_seed_default_providers"
down_revision = "018_task_project_id_nullable"
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
                    'Anthropic (default)',
                    'anthropic',
                    NULL,
                    NULL,
                    true,
                    now()
                ),
                (
                    gen_random_uuid(),
                    'Ollama Cloud',
                    'ollama_cloud',
                    'https://ollama.com',
                    NULL,
                    false,
                    now()
                )
            ON CONFLICT (name) DO NOTHING
            """
        )
    )


def downgrade() -> None:
    # Remove only the rows this migration is responsible for seeding, matching
    # the 004 seed contract. A user who saved an Ollama key (flipping
    # enabled=true + storing a token) loses that row on downgrade; re-running
    # upgrade re-creates the pristine pre-seeded rows.
    op.execute(
        sa.text(
            "DELETE FROM provider_configs "
            "WHERE name IN ('Anthropic (default)', 'Ollama Cloud')"
        )
    )
