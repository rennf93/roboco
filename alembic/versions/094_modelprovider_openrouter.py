"""Add 'openrouter' to the modelprovider enum.

Mirrors migration 090 (kimi): adds ``'openrouter'`` to the PostgreSQL
``modelprovider`` enum so ``ProviderConfigTable.type`` accepts
``ModelProvider.OPENROUTER``. The ``ALTER TYPE ... ADD VALUE IF NOT EXISTS``
must run in its own autocommit transaction — Postgres forbids using a
newly added enum value in the same transaction — so we wrap it in
``autocommit_block()`` and keep this migration scoped to the enum alone.
The companion row-seed lives in migration 095.

Revision ID: 094_modelprovider_openrouter
Revises: 093_playbook_source_program
Create Date: 2026-08-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "094_modelprovider_openrouter"
down_revision = "093_playbook_source_program"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_context().autocommit_block()
    op.execute(
        sa.text("ALTER TYPE modelprovider ADD VALUE IF NOT EXISTS 'openrouter'")
    )
    op.get_context().end_autocommit_block()


def downgrade() -> None:
    # Removing a value from a Postgres enum requires a full type rebuild
    # (create replacement type, alter column, drop old type). Not worth the
    # risk for a rollback — the extra enum value is harmless if unused.
    pass