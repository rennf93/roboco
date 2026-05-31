"""Add products + product_projects tables and additive tasks.product_id.

Introduces the Product entity (a per-cell Project mapping) for board->cells
routing. tasks.product_id is additive + nullable (ON DELETE RESTRICT);
project_id is unchanged. product_projects.team reuses the existing Postgres
"team" enum (create_type=False), unique on (product_id, team).

Revision ID: 016_add_products_product_id
Revises: 015_drop_task_execution_outputs
Create Date: 2026-05-31

Note: the revision id is kept to 27 chars because alembic_version.version_num
is VARCHAR(32); the spec's longer literal ("016_add_products_and_task_product_id",
36 chars) overflows that column and cannot be stamped.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "016_add_products_product_id"
down_revision = "015_drop_task_execution_outputs"
branch_labels = None
depends_on = None

_TEAM_ENUM = sa.Enum(
    "backend", "frontend", "ux_ui", "main_pm", "board", "marketing",
    name="team", create_type=False,
)


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("slug", sa.String(50), nullable=False, unique=True, index=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_by", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id"), nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            nullable=False, server_default=sa.func.now(),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        "product_projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "product_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="CASCADE"),
            nullable=False, index=True,
        ),
        sa.Column("team", _TEAM_ENUM, nullable=False),
        sa.Column(
            "project_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False, index=True,
        ),
        sa.UniqueConstraint(
            "product_id", "team", name="uq_product_projects_product_team"
        ),
    )

    op.add_column(
        "tasks",
        sa.Column(
            "product_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id", ondelete="RESTRICT"), nullable=True,
        ),
    )
    op.create_index("ix_tasks_product_status", "tasks", ["product_id", "status"])


def downgrade() -> None:
    op.drop_index("ix_tasks_product_status", table_name="tasks")
    # FK name follows the project's metadata naming convention
    # (fk_%(table)s_%(column)s_%(referred_table)s), not Postgres's default
    # "tasks_product_id_fkey".
    op.drop_constraint(
        "fk_tasks_product_id_products", "tasks", type_="foreignkey"
    )
    op.drop_column("tasks", "product_id")
    op.drop_table("product_projects")
    op.drop_table("products")
