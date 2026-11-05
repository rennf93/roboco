"""Base-red incidents: one owned P0 incident per red integration base.

Revision ID: 112_base_red_incidents
Revises: 111_ci_runs_reading_key
Create Date: 2026-11-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "112_base_red_incidents"
down_revision = "111_ci_runs_reading_key"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None

_INDEX = "uq_base_red_incidents_one_live"


def upgrade() -> None:
    op.create_table(
        "base_red_incidents",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("base_branch", sa.String(255), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("consecutive_red", sa.Integer, nullable=False),
        sa.Column(
            "owner_task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("owner_agent", sa.String(64), nullable=True),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_reason", sa.Text, nullable=True),
        sa.Column("cleared_by", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_base_red_incidents_project_id", "base_red_incidents", ["project_id"])
    op.create_index(
        _INDEX,
        "base_red_incidents",
        ["project_id", "base_branch"],
        unique=True,
        postgresql_where=sa.text("status IN ('watching', 'open')"),
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="base_red_incidents")
    op.drop_index("ix_base_red_incidents_project_id", table_name="base_red_incidents")
    op.drop_table("base_red_incidents")
