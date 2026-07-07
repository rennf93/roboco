"""Add respawn_tracker.revisit_resets — persist the revisit-reset counter.

Mirrors tracing_resets: the PM-respawn breaker's revisit_resets is kept in
memory only and reset to 0 on restart, re-burning the counter. Persist it so a
restart preserves the count. Additive column with default 0; no data change.

Revision ID: 067_respawn_revisit_resets
Revises: 066_tasks_source_status_idx
Create Date: 2026-07-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "067_respawn_revisit_resets"
down_revision = "066_tasks_source_status_idx"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "respawn_tracker",
        sa.Column("revisit_resets", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("respawn_tracker", "revisit_resets")
