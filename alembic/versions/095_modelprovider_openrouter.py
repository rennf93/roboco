"""Add 'openrouter' to the modelprovider enum.

Mirrors migration 090 (kimi): adds ``'openrouter'`` to the PostgreSQL
``modelprovider`` enum so ``ProviderConfigTable.type`` accepts
``ModelProvider.OPENROUTER``. The ``ALTER TYPE ... ADD VALUE IF NOT EXISTS``
must run in its own autocommit transaction — Postgres forbids using a
newly added enum value in the same transaction — so we wrap it in
``autocommit_block()`` and keep this migration scoped to the enum alone.
The companion row-seed lives in migration 096.

Revision ID: 095_modelprovider_openrouter
Revises: 094_verb_latency_samples
Create Date: 2026-08-24
"""

from __future__ import annotations

from alembic import op

revision = "095_modelprovider_openrouter"
down_revision = "094_verb_latency_samples"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The ALTER TYPE must run and COMMIT before migration 095 seeds a row
    # using 'openrouter' (Postgres forbids using a freshly added enum value
    # in the same transaction that added it). Mirrors migration 090 (kimi)
    # exactly — including the offline --sql rendering the enum-parity test
    # reads. Idempotent via IF NOT EXISTS.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE modelprovider ADD VALUE IF NOT EXISTS 'openrouter'")


def downgrade() -> None:
    # Removing a value from a Postgres enum requires a full type rebuild
    # (create replacement type, alter column, drop old type). Not worth the
    # risk for a rollback — the extra enum value is harmless if unused.
    pass
