"""Add prompt_sessions and prompt_turns tables.

Revision ID: 023_prompt_session_tables
Revises: 022_default_branch_master
Create Date: 2026-06-04

Adds:
- ``promptsessionstatus`` Postgres enum: draft | launched | abandoned
- ``prompt_sessions``: container for a series of LLM prompt turns, with a
  status lifecycle (draft → launched | abandoned).
- ``prompt_turns``: individual turn (message exchange) within a session,
  FK→prompt_sessions with CASCADE delete.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "023_prompt_session_tables"
down_revision = "022_default_branch_master"
branch_labels = None
depends_on = None

_PROMPT_SESSION_STATUS_ENUM = "promptsessionstatus"


def _table_exists(name: str) -> bool:
    """True if ``name`` already exists in the current DB schema.

    Guards against re-running on DBs bootstrapped via Base.metadata.create_all.
    Returns False in offline (--sql) mode so SQL stubs are always emitted.
    """
    if context.is_offline_mode():
        return False
    bind = op.get_bind()
    return inspect(bind).has_table(name)


def _enum_exists(name: str) -> bool:
    """True if a Postgres enum type with ``name`` already exists."""
    if context.is_offline_mode():
        return False
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            "SELECT 1 FROM pg_type WHERE typname = :name AND typtype = 'e'"
        ),
        {"name": name},
    )
    return result.scalar() is not None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. Create the promptsessionstatus enum type (idempotent guard).
    # ------------------------------------------------------------------
    if not _enum_exists(_PROMPT_SESSION_STATUS_ENUM):
        op.execute(
            "CREATE TYPE promptsessionstatus AS ENUM "
            "('draft', 'launched', 'abandoned')"
        )

    # ------------------------------------------------------------------
    # 2. prompt_sessions table.
    # ------------------------------------------------------------------
    if not _table_exists("prompt_sessions"):
        op.create_table(
            "prompt_sessions",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
            ),
            sa.Column(
                "created_by",
                postgresql.UUID(as_uuid=True),
                nullable=True,
            ),
            sa.Column(
                "status",
                sa.Enum(
                    "draft",
                    "launched",
                    "abandoned",
                    name=_PROMPT_SESSION_STATUS_ENUM,
                    create_type=False,
                ),
                nullable=False,
                server_default="draft",
            ),
            sa.Column("system_prompt", sa.Text, nullable=True),
            sa.Column("model", sa.String(100), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                nullable=True,
            ),
        )
        op.create_index(
            "ix_prompt_sessions_created_by", "prompt_sessions", ["created_by"]
        )
        op.create_index(
            "ix_prompt_sessions_status", "prompt_sessions", ["status"]
        )
        op.create_index(
            "ix_prompt_sessions_created_at", "prompt_sessions", ["created_at"]
        )

    # ------------------------------------------------------------------
    # 3. prompt_turns table.
    # ------------------------------------------------------------------
    if not _table_exists("prompt_turns"):
        op.create_table(
            "prompt_turns",
            sa.Column(
                "id",
                postgresql.UUID(as_uuid=True),
                primary_key=True,
            ),
            sa.Column(
                "session_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("prompt_sessions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("role", sa.String(20), nullable=False),
            sa.Column("content", sa.Text, nullable=False),
            sa.Column(
                "turn_index",
                sa.Integer,
                nullable=False,
                server_default="0",
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index(
            "ix_prompt_turns_session_id", "prompt_turns", ["session_id"]
        )
        op.create_index(
            "ix_prompt_turns_session_index",
            "prompt_turns",
            ["session_id", "turn_index"],
        )


def downgrade() -> None:
    # Drop in reverse dependency order.
    op.drop_index("ix_prompt_turns_session_index", table_name="prompt_turns")
    op.drop_index("ix_prompt_turns_session_id", table_name="prompt_turns")
    op.drop_table("prompt_turns")

    op.drop_index("ix_prompt_sessions_created_at", table_name="prompt_sessions")
    op.drop_index("ix_prompt_sessions_status", table_name="prompt_sessions")
    op.drop_index("ix_prompt_sessions_created_by", table_name="prompt_sessions")
    op.drop_table("prompt_sessions")

    op.execute("DROP TYPE IF EXISTS promptsessionstatus")
