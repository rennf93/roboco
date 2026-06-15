"""Add the pitches table — Board proposals the CEO approves to auto-provision.

A pitch is a Board-authored product proposal (problem + proposed solution +
target cells). On CEO approval the system provisions a GitHub repo per target
cell, registers each as a Project (and a Product when multi-cell), and seeds an
initial delivery task to Main PM. Purely additive origination path — the
existing delivery lifecycle is unchanged.

Revision ID: 033_pitches
Revises: 032_company_goals
Create Date: 2026-06-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "033_pitches"
down_revision = "032_company_goals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pitches",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=50), nullable=False, unique=True),
        sa.Column("problem", sa.Text(), nullable=False),
        sa.Column("proposed_solution", sa.Text(), nullable=False),
        sa.Column(
            "target_cells",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="proposed",
        ),
        sa.Column("created_by", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("decided_by", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("decision_notes", sa.Text(), nullable=True),
        sa.Column("provisioned_product_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("provisioned_project_ids", sa.JSON(), nullable=True),
        sa.Column("seed_task_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_pitches_status", "pitches", ["status"])


def downgrade() -> None:
    op.drop_index("ix_pitches_status", table_name="pitches")
    op.drop_table("pitches")
