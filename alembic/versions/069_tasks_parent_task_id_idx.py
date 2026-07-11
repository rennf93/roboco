"""Add ix_tasks_parent_task_id — sibling scans on the claim hot path.

Postgres does not auto-index FK columns, so every sibling lookup
(``get_subtasks``, the sequence claim guard's blocking-sibling probe, the
dispatch merge/lane barriers) was a Seq Scan over tasks. The sequence guard
runs on every PENDING/NEEDS_REVISION claim, so the scan sat on the hottest
verb. Plain btree; additive; no data change.

Revision ID: 069_tasks_parent_task_id_idx
Revises: 068_tasks_constraints_column
Create Date: 2026-07-10
"""

from __future__ import annotations

from alembic import op

revision = "069_tasks_parent_task_id_idx"
down_revision = "068_tasks_constraints_column"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_tasks_parent_task_id", "tasks", ["parent_task_id"])


def downgrade() -> None:
    op.drop_index("ix_tasks_parent_task_id", table_name="tasks")
