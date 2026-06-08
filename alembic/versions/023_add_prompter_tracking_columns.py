"""Add prompter origin tracking columns to tasks table.

Adds `source` (varchar 50, default 'manual') and `confirmed_by_human`
(boolean, default false) to support the Prompter conversational assistant
feature. Prompter-originated tasks require human confirmation before entering
the workflow.

Revision ID: 023_add_prompter_tracking_columns
Revises: 022_default_branch_master
Create Date: 2026-06-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "023_add_prompter_tracking_columns"
down_revision = "022_default_branch_master"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("source", sa.String(length=50), server_default="manual", nullable=False),
    )
    op.add_column(
        "tasks",
        sa.Column("confirmed_by_human", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("tasks", "confirmed_by_human")
    op.drop_column("tasks", "source")
