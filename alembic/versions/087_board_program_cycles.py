"""Add board_program_cycles table — the Board Program registry's LEARN ledger.

One row per program cycle (roadmap, x_feature, and every later registry
entry). ``BoardProgramEngine`` dedups one open cycle per program off this
table (``closed_at IS NULL``) instead of each engine growing its own
open-cycle query, and accrues per-item approve/reject outcomes into
``decisions`` so the next cycle's exploration prompt can reference prior
rejections. Additive; inert until the Task-3 service starts writing to it.

Revision ID: 087_board_program_cycles
Revises: 086_enable_gemini_provider
Create Date: 2026-07-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "087_board_program_cycles"
down_revision = "086_enable_gemini_provider"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None


def upgrade() -> None:
    op.create_table(
        "board_program_cycles",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("program_key", sa.String(length=40), nullable=False),
        sa.Column("exploration_task_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "opened_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("items_proposed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_approved", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("items_rejected", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "decisions", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")
        ),
        sa.ForeignKeyConstraint(
            ["exploration_task_id"], ["tasks.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_board_program_cycles_program_key",
        "board_program_cycles",
        ["program_key"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_board_program_cycles_program_key", table_name="board_program_cycles"
    )
    op.drop_table("board_program_cycles")
