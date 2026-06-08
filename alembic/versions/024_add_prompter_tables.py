"""Add prompter_sessions, prompter_messages, and task_drafts tables.

Adds three new tables to support the Prompter conversational assistant
feature with DB-persisted conversation history and task draft tracking:

- prompter_sessions: links a conversation session to an authenticated agent
- prompter_messages: stores the full message history (user + assistant turns)
- task_drafts: stores structured task drafts extracted from conversations;
  links to a real Task once the human confirms via /confirm endpoint

Revision ID: 024_add_prompter_tables
Revises: 023_add_prompter_tracking_columns
Create Date: 2026-06-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "024_add_prompter_tables"
down_revision = "023_add_prompter_tracking_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- prompter_sessions -----------------------------------------------
    op.create_table(
        "prompter_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "agent_id",
            UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "active",
                "draft_ready",
                "confirmed",
                "abandoned",
                name="promptersessionstatus",
            ),
            nullable=False,
            server_default="active",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            onupdate=sa.func.now(),
        ),
    )
    op.create_index("ix_prompter_sessions_agent_id", "prompter_sessions", ["agent_id"])
    op.create_index(
        "ix_prompter_sessions_status", "prompter_sessions", ["status"]
    )

    # --- prompter_messages -----------------------------------------------
    op.create_table(
        "prompter_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("prompter_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "role",
            sa.Enum("user", "assistant", "system", name="promptermessagerole"),
            nullable=False,
        ),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_prompter_messages_session_id", "prompter_messages", ["session_id"]
    )
    op.create_index(
        "ix_prompter_messages_session_created",
        "prompter_messages",
        ["session_id", "created_at"],
    )

    # --- task_drafts -----------------------------------------------------
    op.create_table(
        "task_drafts",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("prompter_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("draft_data", JSONB, nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "task_id",
            UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=True,
            onupdate=sa.func.now(),
        ),
    )
    op.create_index("ix_task_drafts_session_id", "task_drafts", ["session_id"])
    op.create_index("ix_task_drafts_task_id", "task_drafts", ["task_id"])


def downgrade() -> None:
    op.drop_table("task_drafts")
    op.drop_index("ix_prompter_messages_session_created", "prompter_messages")
    op.drop_index("ix_prompter_messages_session_id", "prompter_messages")
    op.drop_table("prompter_messages")
    op.drop_index("ix_prompter_sessions_status", "prompter_sessions")
    op.drop_index("ix_prompter_sessions_agent_id", "prompter_sessions")
    op.drop_table("prompter_sessions")
    op.execute("DROP TYPE IF EXISTS promptersessionstatus")
    op.execute("DROP TYPE IF EXISTS promptermessagerole")
