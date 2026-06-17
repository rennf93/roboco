"""Add 'pr_reviewer' to the postgres agentrole enum.

The PR reviewer (``Role.PR_REVIEWER`` in foundation/identity) is a read-only
agent that reviews inbound external/fork PRs. Seeding/spawning its agent row
requires the postgres ``agentrole`` enum to carry the value. Mirrors migration
034's pattern.

Revision ID: 037_agentrole_pr_reviewer
Revises: 036_ac_ids_and_parent_refs
Create Date: 2026-06-16
"""

from __future__ import annotations

from alembic import op

revision = "037_agentrole_pr_reviewer"
down_revision = "036_ac_ids_and_parent_refs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Unguarded (renders in offline --sql so the enum-migration-parity test
    # sees it) and idempotent. PG 16 permits ADD VALUE inside a transaction.
    op.execute("ALTER TYPE agentrole ADD VALUE IF NOT EXISTS 'pr_reviewer'")


def downgrade() -> None:
    # Postgres does not support removing enum values without a destructive
    # type recreation. Forward-only by design (see migration 034).
    pass
