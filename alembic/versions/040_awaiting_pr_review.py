"""Add 'awaiting_pr_review' to the postgres taskstatus enum.

The in-path PR-review gate (``Status.AWAITING_PR_REVIEW`` in
foundation/policy/lifecycle and ``TaskStatus`` in models/base) inserts a
reviewer sign-off between the assembled-PR submit and the PM merge. Persisting
a task in that state requires the postgres ``taskstatus`` enum to carry the
value. Mirrors migration 037's pattern (forward-only enum ADD VALUE).

Revision ID: 040_awaiting_pr_review
Revises: 039_seed_grok_provider
Create Date: 2026-06-20

NOTE: the revision id is kept well under 32 chars — alembic's
``alembic_version.version_num`` column is ``VARCHAR(32)`` and a longer id raises
``value too long for type character varying(32)`` when alembic records it.
"""

from __future__ import annotations

from alembic import op

revision = "040_awaiting_pr_review"
down_revision = "039_seed_grok_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Unguarded (renders in offline --sql so the enum-migration-parity test
    # sees it) and idempotent. PG 16 permits ADD VALUE inside a transaction.
    op.execute("ALTER TYPE taskstatus ADD VALUE IF NOT EXISTS 'awaiting_pr_review'")


def downgrade() -> None:
    # Postgres does not support removing enum values without a destructive
    # type recreation. Forward-only by design (see migrations 034 / 037).
    pass
