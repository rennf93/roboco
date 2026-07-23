"""Add 'gemini' to the postgres modelprovider enum.

Gemini (``ModelProvider.GEMINI`` — Google's OAuth-authenticated ``gemini`` CLI)
is a new agent backend. Seeding its provider row (migration 085) and routing
agents to it requires the postgres ``modelprovider`` enum to carry the value.
Mirrors the enum-add pattern of migration 038 (grok); the row seed is split
into 085 because a newly added enum value cannot be used in the same
transaction that adds it.

RE-CHAIN CAVEAT: this task built against a checkout where 081 was head, so it
originally numbered these 082/083. Two sibling worktrees landed 082 (routing)
and 083 (codex `seed_openai_provider`) first — this pair was renumbered
084/085 on top of them post-hoc, in this worktree only, to keep a single
linear head: routing(082) -> codex(083) -> gemini(084/085). Neither 082 nor
083 exists in THIS checkout, so this worktree's own migration-graph-integrity
and enum-migration-parity tests fail on the missing siblings until the real
merge lands all three branches together.

Revision ID: 084_modelprovider_gemini
Revises: 083_seed_openai_provider
Create Date: 2026-07-23
"""

from __future__ import annotations

from alembic import op

revision = "084_modelprovider_gemini"
down_revision = "083_seed_openai_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The new value must be COMMITTED before migration 085 inserts a row using
    # it: alembic runs the whole upgrade in a single transaction, and Postgres
    # forbids using a freshly added enum value in the same transaction that
    # added it (UnsafeNewEnumValueUsageError). autocommit_block commits the
    # ALTER on its own so 'gemini' is usable downstream. Still renders the
    # ALTER TYPE in offline --sql, so the enum-migration-parity test sees it.
    # Idempotent via IF NOT EXISTS.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE modelprovider ADD VALUE IF NOT EXISTS 'gemini'")


def downgrade() -> None:
    # Postgres does not support removing enum values without a destructive
    # type recreation. Forward-only by design (see migration 037).
    pass
