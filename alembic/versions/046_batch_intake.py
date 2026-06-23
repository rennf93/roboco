"""Sequenced batch intake: tasks.batch_id + collision descriptor columns.

The "Mega task" groups a batch of top-level tasks under a shared ``batch_id``;
the three descriptors are the per-task collision surface the SequencingService
reads to wire dependency waves. Pure schema change, no backfill — existing rows
get NULL ``batch_id`` / NULL ``intends_to_touch`` and ``false`` for both bool
descriptors (a non-batch task declares no collision surface).

Revision ID: 046_batch_intake
Revises: 045_observability_rework
Create Date: 2026-06-23

NOTE: revision id is 16 chars — alembic's ``alembic_version.version_num`` is
``VARCHAR(32)`` and a longer id raises at record time.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "046_batch_intake"
down_revision = "045_observability_rework"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("batch_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("intends_to_touch", postgresql.ARRAY(sa.String()), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "adds_migration", sa.Boolean(), nullable=False, server_default="false"
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "touches_shared", sa.Boolean(), nullable=False, server_default="false"
        ),
    )
    op.create_index("ix_tasks_batch_id", "tasks", ["batch_id"])


def downgrade() -> None:
    op.drop_index("ix_tasks_batch_id", table_name="tasks")
    op.drop_column("tasks", "touches_shared")
    op.drop_column("tasks", "adds_migration")
    op.drop_column("tasks", "intends_to_touch")
    op.drop_column("tasks", "batch_id")
