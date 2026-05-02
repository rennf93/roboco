"""Add gateway-coordination columns to tasks: claimant lock, heartbeat,
pre-block snapshot, acceptance criteria status, qa evidence inspection flag.

Revision ID: 006_gateway_columns
Revises: 005_blocker_raised_by
Create Date: 2026-05-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "006_gateway_columns"
down_revision = "005_blocker_raised_by"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add gateway columns to the tasks table."""
    op.add_column(
        "tasks",
        sa.Column("active_claimant_id", sa.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("pre_block_state", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("pre_block_assignee", sa.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("pre_block_metadata", sa.JSON(), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "acceptance_criteria_status",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "qa_evidence_inspected",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )

    op.create_foreign_key(
        "fk_tasks_active_claimant_id_agents",
        "tasks",
        "agents",
        ["active_claimant_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_tasks_pre_block_assignee_agents",
        "tasks",
        "agents",
        ["pre_block_assignee"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_tasks_active_claimant_heartbeat",
        "tasks",
        ["active_claimant_id", "last_heartbeat_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_active_claimant_heartbeat", table_name="tasks")
    op.drop_constraint(
        "fk_tasks_pre_block_assignee_agents", "tasks", type_="foreignkey"
    )
    op.drop_constraint(
        "fk_tasks_active_claimant_id_agents", "tasks", type_="foreignkey"
    )
    op.drop_column("tasks", "qa_evidence_inspected")
    op.drop_column("tasks", "acceptance_criteria_status")
    op.drop_column("tasks", "pre_block_metadata")
    op.drop_column("tasks", "pre_block_assignee")
    op.drop_column("tasks", "pre_block_state")
    op.drop_column("tasks", "last_heartbeat_at")
    op.drop_column("tasks", "active_claimant_id")
