"""Add prompter_conversations and prompter_messages tables.

Revision ID: 015_prompter_tables
Revises: 014_drop_pm_approvals
Create Date: 2026-06-03

Adds two tables for the human-facing Prompter feature:
- ``prompter_conversations``: one row per conversation thread.
- ``prompter_messages``: individual user/assistant turns; cascade-deleted
  when the parent conversation is removed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "015_prompter_tables"
down_revision = "014_drop_pm_approvals"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "prompter_conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_id", sa.String(100), nullable=False),
        sa.Column("title", sa.String(500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_prompter_conv_agent_created",
        "prompter_conversations",
        ["agent_id", "created_at"],
    )

    op.create_table(
        "prompter_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("prompter_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "ix_prompter_msg_conv_created",
        "prompter_messages",
        ["conversation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_prompter_msg_conv_created", table_name="prompter_messages")
    op.drop_table("prompter_messages")
    op.drop_index("ix_prompter_conv_agent_created", table_name="prompter_conversations")
    op.drop_table("prompter_conversations")
