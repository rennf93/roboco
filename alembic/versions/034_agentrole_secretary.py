"""Add 'secretary' to the postgres agentrole enum.

The Secretary is the CEO's conversational chief-of-staff (``Role.SECRETARY`` in
foundation/identity). Seeding/spawning its agent row requires the postgres
``agentrole`` enum to carry the value. Mirrors migration 025's pattern.

Revision ID: 034_agentrole_secretary
Revises: 033_pitches
Create Date: 2026-06-15
"""

from __future__ import annotations

from alembic import op

revision = "034_agentrole_secretary"
down_revision = "033_pitches"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Unguarded (renders in offline --sql so the enum-migration-parity test
    # sees it) and idempotent. PG 16 permits ADD VALUE inside a transaction.
    op.execute("ALTER TYPE agentrole ADD VALUE IF NOT EXISTS 'secretary'")


def downgrade() -> None:
    # Postgres does not support removing enum values without a destructive
    # type recreation. Forward-only by design (see migration 025).
    pass
