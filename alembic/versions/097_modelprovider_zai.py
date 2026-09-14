"""Add 'zai' to the modelprovider enum.

Mirrors migration 095 (openrouter): adds ``'zai'`` to the PostgreSQL
``modelprovider`` enum so ``ProviderConfigTable.type`` accepts
``ModelProvider.ZAI``. The ``ALTER TYPE ... ADD VALUE IF NOT EXISTS``
must run in its own autocommit transaction — Postgres forbids using a
newly added enum value in the same transaction — so we wrap it in
``autocommit_block()`` and keep this migration scoped to the enum alone.
The companion row-seed lives in migration 098.

Revision ID: 097_modelprovider_zai
Revises: 096_seed_openrouter_provider
Create Date: 2026-09-14
"""

from __future__ import annotations

from alembic import op

revision = "097_modelprovider_zai"
down_revision = "096_seed_openrouter_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The ALTER TYPE must run and COMMIT before migration 098 seeds a row
    # using 'zai' (Postgres forbids using a freshly added enum value
    # in the same transaction that added it). Mirrors migration 095
    # (openrouter) exactly — including the offline --sql rendering the
    # enum-parity test reads. Idempotent via IF NOT EXISTS.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE modelprovider ADD VALUE IF NOT EXISTS 'zai'")


def downgrade() -> None:
    # Removing a value from a Postgres enum requires a full type rebuild
    # (create replacement type, alter column, drop old type). Not worth the
    # risk for a rollback — the extra enum value is harmless if unused.
    pass
