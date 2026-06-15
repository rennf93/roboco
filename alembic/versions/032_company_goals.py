"""Add the company_goals singleton charter table.

A single CEO-owned row — north star, prioritized objectives, constraints, and
operating policy — injected compactly into every agent's context_briefing so all
work is goal-aware. Seeded with one empty row at the all-zeros singleton id; the
CEO populates it via the API (CEO-only writes).

Revision ID: 032_company_goals
Revises: 031_rag_chunks_fulltext
Create Date: 2026-06-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "032_company_goals"
down_revision = "031_rag_chunks_fulltext"
branch_labels = None
depends_on = None

_SINGLETON_ID = "00000000-0000-0000-0000-000000000000"


def upgrade() -> None:
    op.create_table(
        "company_goals",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("north_star", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "objectives",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column(
            "constraints",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column(
            "operating_policy",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'::json"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_by", sa.UUID(as_uuid=True), nullable=True),
    )
    # Seed the singleton row; the column server defaults fill the rest.
    op.execute(f"INSERT INTO company_goals (id) VALUES ('{_SINGLETON_ID}')")


def downgrade() -> None:
    op.drop_table("company_goals")
