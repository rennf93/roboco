"""Add 'hummin' to the postgres modelprovider enum.

Hummin (``ModelProvider.HUMMIN`` — the GLM-native ``hummin`` CLI running
headless in Docker, key-authenticated against the GLM Coding Plan) is a new
agent backend. Seeding its provider row (migration 102) and routing agents to
it requires the postgres ``modelprovider`` enum to carry the value. Mirrors
the enum-add pattern of migration 090 (kimi); the row seed is split into 102
because a newly added enum value cannot be used in the same transaction that
adds it.

Revision ID: 101_modelprovider_hummin
Revises: 100_seed_nebius_provider
Create Date: 2026-09-17
"""

from __future__ import annotations

from alembic import op

revision = "101_modelprovider_hummin"
down_revision = "100_seed_nebius_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The new value must be COMMITTED before migration 102 inserts a row using
    # it: alembic runs the whole upgrade in a single transaction, and Postgres
    # forbids using a freshly added enum value in the same transaction that
    # added it (UnsafeNewEnumValueUsageError). autocommit_block commits the
    # ALTER on its own so 'hummin' is usable downstream. Still renders the ALTER
    # TYPE in offline --sql, so the enum-migration-parity test sees it.
    # Idempotent via IF NOT EXISTS.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE modelprovider ADD VALUE IF NOT EXISTS 'hummin'")


def downgrade() -> None:
    # Postgres does not support removing enum values without a destructive
    # type recreation. Forward-only by design (see migration 037).
    pass
