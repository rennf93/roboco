"""Add 'prompter' to the postgres agentrole enum.

The intake interviewer is a new agent role (``Role.PROMPTER`` in
foundation/identity). Seeding/spawning its agent row requires the postgres
``agentrole`` enum to carry the value. Mirrors migration 012's pattern.

Revision ID: 025_agentrole_prompter
Revises: 024_add_prompter_tables
Create Date: 2026-06-08
"""

from __future__ import annotations

from alembic import context, op

revision = "025_agentrole_prompter"
down_revision = "024_add_prompter_tables"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if context.is_offline_mode():
        # Offline mode: skip — only runs when connected to a real DB.
        return
    # ADD VALUE IF NOT EXISTS is idempotent in postgres >= 9.6.
    op.execute("ALTER TYPE agentrole ADD VALUE IF NOT EXISTS 'prompter'")


def downgrade() -> None:
    # Postgres does not support removing enum values without a destructive
    # type recreation. Forward-only by design (see migration 012).
    pass
