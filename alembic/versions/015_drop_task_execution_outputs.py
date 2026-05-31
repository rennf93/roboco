"""Drop unused Task columns execution_log and outputs.

Pre-gateway columns with zero readers/writers (verified 2026-05-31). The
gateway choreographer tracks execution progress via progress_updates and
task artifacts via commits/documents; execution_log and the per-task file
`outputs` list were never populated and have no readers in code, tests, or
migrations.

Revision ID: 015_drop_task_execution_outputs
Revises: 014_drop_pm_approvals
Create Date: 2026-05-31
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "015_drop_task_execution_outputs"
down_revision = "014_drop_pm_approvals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("tasks", "execution_log")
    op.drop_column("tasks", "outputs")


def downgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("execution_log", sa.JSON, nullable=False, server_default="{}"),
    )
    op.add_column(
        "tasks",
        sa.Column("outputs", sa.JSON, nullable=False, server_default="[]"),
    )
