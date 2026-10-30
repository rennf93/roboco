"""Add 'devops' to the postgres agentrole enum.

The DevOps floater (``Role.DEVOPS`` in foundation/identity) is a floating
infra author + second PR-gate reviewer, seeded as the board-team slug
``devops-1``. Seeding/spawning its agent row requires the postgres
``agentrole`` enum to carry the value. Mirrors migration 037's pattern.
The TEAM enum needs no change: the floater pattern fits the existing
'board' team.

Revision ID: 103_agentrole_devops
Revises: 102_seed_hummin_provider
Create Date: 2026-09-20
"""

from __future__ import annotations

from alembic import op

revision = "103_agentrole_devops"
down_revision = "102_seed_hummin_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Unguarded (renders in offline --sql so the enum-migration-parity test
    # sees it) and idempotent. PG 16 permits ADD VALUE inside a transaction.
    op.execute("ALTER TYPE agentrole ADD VALUE IF NOT EXISTS 'devops'")


def downgrade() -> None:
    # Postgres does not support removing enum values without a destructive
    # type recreation. Forward-only by design (see migration 034).
    pass
