"""Create gateway_triggers table — records every dispatcher decision
(spawn, queue, drop_stale, cooldown) for observability and tuning.

Revision ID: 007_gateway_triggers_table
Revises: 006_gateway_columns
Create Date: 2026-05-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "007_gateway_triggers_table"
down_revision = "006_gateway_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gateway_triggers",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("trigger_kind", sa.String(length=40), nullable=False),
        sa.Column("trigger_id", sa.String(length=80), nullable=True),
        sa.Column("task_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("target_role", sa.String(length=40), nullable=False),
        sa.Column("decision", sa.String(length=20), nullable=False),
        sa.Column("decision_reason", sa.String(length=200), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_gateway_triggers_task_id", "gateway_triggers", ["task_id"])
    op.create_index(
        "ix_gateway_triggers_created_at", "gateway_triggers", ["created_at"]
    )
    op.create_index(
        "ix_gateway_triggers_kind_decision",
        "gateway_triggers",
        ["trigger_kind", "decision"],
    )
    op.create_foreign_key(
        "fk_gateway_triggers_task_id_tasks",
        "gateway_triggers",
        "tasks",
        ["task_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_gateway_triggers_task_id_tasks", "gateway_triggers", type_="foreignkey"
    )
    op.drop_index("ix_gateway_triggers_kind_decision", table_name="gateway_triggers")
    op.drop_index("ix_gateway_triggers_created_at", table_name="gateway_triggers")
    op.drop_index("ix_gateway_triggers_task_id", table_name="gateway_triggers")
    op.drop_table("gateway_triggers")
