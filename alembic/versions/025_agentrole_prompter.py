"""Add 'prompter' to the postgres agentrole enum.

The intake interviewer is a new agent role (``Role.PROMPTER`` in
foundation/identity). Seeding/spawning its agent row requires the postgres
``agentrole`` enum to carry the value. Mirrors migration 012's pattern.

Revision ID: 025_agentrole_prompter
Revises: 024_add_prompter_tables
Create Date: 2026-06-08
"""

from __future__ import annotations

from alembic import op

revision = "025_agentrole_prompter"
down_revision = "024_add_prompter_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Unguarded (renders in offline --sql so the enum-migration-parity test
    # sees it) and idempotent. PG 16 permits ADD VALUE inside a transaction —
    # same pattern as migration 020's backfill.
    op.execute("ALTER TYPE agentrole ADD VALUE IF NOT EXISTS 'prompter'")


def downgrade() -> None:
    # Postgres does not support removing enum values without a destructive
    # type recreation. Forward-only by design (see migration 012).
    pass
