"""Align postgres agentrole/team enums with foundation/identity.

Adds any enum value the foundation declares that postgres lacks. Postgres
enum values cannot be removed without a destructive recreation, so the
inverse direction (postgres has extras the foundation lacks) is handled
in foundation by keeping the legacy value (e.g., Team.MARKETING).

Revision ID: 012_align_agentrole_foundation
Revises: 011_drop_quarantined_state
Create Date: 2026-05-10
"""

from __future__ import annotations

from alembic import context, op

revision = "012_align_agentrole_foundation"
down_revision = "011_drop_quarantined_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if context.is_offline_mode():
        # Offline mode: skip — only runs when connected to a real DB.
        return
    # Add 'system' to agentrole enum if missing. ALTER TYPE ... ADD VALUE
    # IF NOT EXISTS is idempotent in postgres >= 9.6.
    op.execute("ALTER TYPE agentrole ADD VALUE IF NOT EXISTS 'system'")
    # Note: every foundation Team value is expected to be already in
    # postgres (verified by scripts/verify_postgres_enums.py when DB
    # access is available). If a future Team is added to foundation,
    # add an ALTER TYPE call for it here.


def downgrade() -> None:
    # Postgres does not support removing enum values without a destructive
    # type recreation. This migration is forward-only by design.
    pass
