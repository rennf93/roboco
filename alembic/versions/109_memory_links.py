"""Add memory_links (Decisions training corpus, spec 12.1 Wave 5).

Links memory-family decision rows to the knowledge item the decision
produced (the distilled learning's source URI, or the board-review draft
task created from a vault note) so the labeler can grade the decision
against that item's later retrieval or fate. Purely additive.

Revision ID: 109_memory_links
Revises: 108_ci_history
Create Date: 2026-09-27
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "109_memory_links"
down_revision = "108_ci_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "memory_links",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("pilot", sa.String(60), nullable=False),
        sa.Column("session_id", sa.String(280), nullable=False),
        sa.Column("source", sa.String(500), nullable=False),
    )
    op.create_index(
        "ix_memory_links_created_at", "memory_links", ["created_at"]
    )
    op.create_index(
        "ix_memory_links_pilot_session", "memory_links", ["pilot", "session_id"]
    )
    op.create_index("ix_memory_links_source", "memory_links", ["source"])


def downgrade() -> None:
    op.drop_table("memory_links")
