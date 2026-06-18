"""Add 'grok' to the postgres modelprovider enum.

Grok (``ModelProvider.GROK`` — xAI's OpenAI-compatible grok-build-0.1) is a new
agent backend. Seeding its provider row (migration 039) and routing agents to it
requires the postgres ``modelprovider`` enum to carry the value. Mirrors the
enum-add pattern of migration 037; the row seed is split into 039 because a
newly added enum value cannot be used in the same transaction that adds it.

Revision ID: 038_modelprovider_grok
Revises: 037_agentrole_pr_reviewer
Create Date: 2026-06-18
"""

from __future__ import annotations

from alembic import op

revision = "038_modelprovider_grok"
down_revision = "037_agentrole_pr_reviewer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Unguarded (renders in offline --sql so the enum-migration-parity test
    # sees it) and idempotent. PG 16 permits ADD VALUE inside a transaction.
    op.execute("ALTER TYPE modelprovider ADD VALUE IF NOT EXISTS 'grok'")


def downgrade() -> None:
    # Postgres does not support removing enum values without a destructive
    # type recreation. Forward-only by design (see migration 037).
    pass
