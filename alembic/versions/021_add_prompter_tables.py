"""Add prompter_conversations and prompter_messages tables.

Revision ID: 021_add_prompter_tables
Revises: 020_backfill_enum_values
Create Date: 2026-06-04

Adds two tables to support the Prompter chat feature:

  - ``prompter_conversations`` — a persistent chat thread (id, title,
    message_count, created_at, updated_at).
  - ``prompter_messages`` — individual user/assistant turns within a
    conversation (id, conversation_id FK, role, content, model_used,
    created_at).  Cascade-deletes when the parent conversation is removed.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "021_add_prompter_tables"
down_revision = "020_backfill_enum_values"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # prompter_conversations
    # ------------------------------------------------------------------
    op.create_table(
        "prompter_conversations",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(255), nullable=False, server_default=""),
        sa.Column("message_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prompter_conversations")),
    )
    op.create_index(
        "ix_prompter_conv_created_at",
        "prompter_conversations",
        ["created_at"],
    )
    op.create_index(
        "ix_prompter_conv_updated_at",
        "prompter_conversations",
        ["updated_at"],
    )

    # ------------------------------------------------------------------
    # prompter_messages
    # ------------------------------------------------------------------
    op.create_table(
        "prompter_messages",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("model_used", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["prompter_conversations.id"],
            name=op.f("fk_prompter_messages_conversation_id_prompter_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_prompter_messages")),
    )
    op.create_index(
        "ix_prompter_messages_conversation_id",
        "prompter_messages",
        ["conversation_id"],
    )
    op.create_index(
        "ix_prompter_messages_created_at",
        "prompter_messages",
        ["created_at"],
    )
    op.create_index(
        "ix_prompter_msg_conv_created",
        "prompter_messages",
        ["conversation_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("prompter_messages")
    op.drop_table("prompter_conversations")
