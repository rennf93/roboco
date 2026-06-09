"""026_token_usage_tables

Create token usage instrumentation tables:
- agent_spawn_sessions: tracks each agent container spawn with token totals
- token_usage_snapshots: periodic snapshots of token usage per session
- daily_usage_rollups: aggregated daily usage per agent/team/model

Revision ID: 026_token_usage_tables
Revises: 025_agentrole_prompter
Create Date: 2026-06-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "026_token_usage_tables"
down_revision = "025_agentrole_prompter"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # agent_spawn_sessions
    # One row per container spawn. Opened on spawn, closed on stop.
    # ------------------------------------------------------------------
    op.create_table(
        "agent_spawn_sessions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("agent_slug", sa.String(100), nullable=False),
        sa.Column("team", sa.String(50), nullable=False),
        sa.Column("role", sa.String(50), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("task_id", sa.String(36), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        # BIGINT for token counts — they can exceed INT32 for long sessions
        sa.Column("tokens_input", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("tokens_output", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column(
            "tokens_cache_read", sa.BigInteger, nullable=False, server_default="0"
        ),
        sa.Column(
            "tokens_cache_write", sa.BigInteger, nullable=False, server_default="0"
        ),
        sa.Column("exit_reason", sa.String(100), nullable=True),
        sa.Column("estimated_cost_usd", sa.Float, nullable=True),
    )

    # Indexes for common query patterns
    op.create_index(
        "ix_agent_spawn_sessions_agent_slug",
        "agent_spawn_sessions",
        ["agent_slug"],
    )
    op.create_index(
        "ix_agent_spawn_sessions_started_at",
        "agent_spawn_sessions",
        ["started_at"],
    )
    op.create_index(
        "ix_agent_spawn_sessions_ended_at",
        "agent_spawn_sessions",
        ["ended_at"],
    )
    op.create_index(
        "ix_agent_spawn_sessions_team",
        "agent_spawn_sessions",
        ["team"],
    )

    # ------------------------------------------------------------------
    # token_usage_snapshots
    # Periodic snapshots (every ~60s) of token counts for active sessions.
    # ------------------------------------------------------------------
    op.create_table(
        "token_usage_snapshots",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "agent_spawn_session_id",
            UUID(as_uuid=True),
            sa.ForeignKey(
                "agent_spawn_sessions.id", ondelete="CASCADE", name="fk_snapshot_session"
            ),
            nullable=False,
        ),
        sa.Column(
            "snapshotted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("tokens_input", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("tokens_output", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column(
            "tokens_cache_read", sa.BigInteger, nullable=False, server_default="0"
        ),
        sa.Column(
            "tokens_cache_write", sa.BigInteger, nullable=False, server_default="0"
        ),
    )

    op.create_index(
        "ix_token_usage_snapshots_session_id",
        "token_usage_snapshots",
        ["agent_spawn_session_id"],
    )
    op.create_index(
        "ix_token_usage_snapshots_snapshotted_at",
        "token_usage_snapshots",
        ["snapshotted_at"],
    )

    # ------------------------------------------------------------------
    # daily_usage_rollups
    # Pre-aggregated daily totals per (date, agent_slug, team, model).
    # Populated by the sweeper; upserted on each sweep so re-runs are safe.
    # ------------------------------------------------------------------
    op.create_table(
        "daily_usage_rollups",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("date", sa.Date, nullable=False),
        sa.Column("agent_slug", sa.String(100), nullable=False),
        sa.Column("team", sa.String(50), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("tokens_input", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column("tokens_output", sa.BigInteger, nullable=False, server_default="0"),
        sa.Column(
            "tokens_cache_read", sa.BigInteger, nullable=False, server_default="0"
        ),
        sa.Column(
            "tokens_cache_write", sa.BigInteger, nullable=False, server_default="0"
        ),
        sa.Column("total_cost_usd", sa.Float, nullable=False, server_default="0"),
        sa.Column("session_count", sa.Integer, nullable=False, server_default="0"),
    )

    # Unique constraint enables ON CONFLICT upsert in the sweeper
    op.create_unique_constraint(
        "uq_daily_rollup_date_agent_team_model",
        "daily_usage_rollups",
        ["date", "agent_slug", "team", "model"],
    )
    op.create_index(
        "ix_daily_rollups_date",
        "daily_usage_rollups",
        ["date"],
    )
    op.create_index(
        "ix_daily_rollups_agent_slug",
        "daily_usage_rollups",
        ["agent_slug"],
    )


def downgrade() -> None:
    op.drop_table("daily_usage_rollups")
    op.drop_table("token_usage_snapshots")
    op.drop_table("agent_spawn_sessions")
