"""Add 'kimi' to the postgres modelprovider enum.

Kimi (``ModelProvider.KIMI`` — Moonshot's subscription-authenticated
``kimi``/kimi-code CLI) is a new agent backend. Seeding its provider row
(migration 091) and routing agents to it requires the postgres
``modelprovider`` enum to carry the value. Mirrors the enum-add pattern of
migration 084 (gemini); the row seed is split into 091 because a newly added
enum value cannot be used in the same transaction that adds it.

Revision ID: 090_modelprovider_kimi
Revises: 089_board_cycle_ntp_reason
Create Date: 2026-07-28
"""

from __future__ import annotations

from alembic import op

revision = "090_modelprovider_kimi"
down_revision = "089_board_cycle_ntp_reason"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The new value must be COMMITTED before migration 091 inserts a row using
    # it: alembic runs the whole upgrade in a single transaction, and Postgres
    # forbids using a freshly added enum value in the same transaction that
    # added it (UnsafeNewEnumValueUsageError). autocommit_block commits the
    # ALTER on its own so 'kimi' is usable downstream. Still renders the ALTER
    # TYPE in offline --sql, so the enum-migration-parity test sees it.
    # Idempotent via IF NOT EXISTS.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE modelprovider ADD VALUE IF NOT EXISTS 'kimi'")


def downgrade() -> None:
    # Postgres does not support removing enum values without a destructive
    # type recreation. Forward-only by design (see migration 037).
    pass
