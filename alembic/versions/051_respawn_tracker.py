"""Add the respawn_tracker table — durable PM-respawn loop counter.

``AgentOrchestrator._pm_respawn_tracker`` is the circuit breaker against
respawning the same PM on the same task forever. Kept only in memory it reset
to ``count=1`` on every orchestrator restart, re-burning the whole strike
threshold against a still-wedged task. This table is its write-through mirror,
restored at startup. Composite PK ``(agent_slug, task_id)`` matches the
in-memory dict key. ``task_id`` is deliberately NOT a FK to ``tasks``: the
startup loader validates against live tasks instead, so a stale counter cannot
resurrect against a fixed/deleted task.

Revision ID: 051_respawn_tracker
Revises: 050_playbooks
Create Date: 2026-06-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "051_respawn_tracker"
down_revision = "050_playbooks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "respawn_tracker",
        sa.Column("agent_slug", sa.String(length=64), nullable=False),
        sa.Column("task_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("last_status", sa.String(length=64), nullable=True),
        sa.Column("last_check", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tracing_resets", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("notified", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("agent_slug", "task_id"),
    )
    op.create_index("ix_respawn_tracker_last_check", "respawn_tracker", ["last_check"])


def downgrade() -> None:
    op.drop_index("ix_respawn_tracker_last_check", table_name="respawn_tracker")
    op.drop_table("respawn_tracker")
