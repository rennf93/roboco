"""Add member_performance_daily — the granular per-member scorecard rollup.

One row per (date, member_kind, agent_slug) populated by the orchestrator's
sweeper from data already captured (agent_spawn_sessions + audit_log). Serves
the per-member / team / org scorecards without re-scanning raw task lists. The
CEO is a first-class ``member_kind='ceo'`` row (agent_slug='' — Postgres UNIQUE
treats NULL as distinct, so the empty string keeps the CEO row unique).

Includes the four CEO-approved extra metrics (qa pass-rate counts, escalations,
blocked-others, idle_seconds) plus blocked_seconds. All columns DEFAULT 0 so a
fresh/partial row reads as zeros, never NULL.

Revision ID: 056_member_perf_daily
Revises: 055_spawn_session_turns
Create Date: 2026-07-01
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "056_member_perf_daily"
down_revision = "055_spawn_session_turns"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None


def _int(name: str) -> sa.Column:
    return sa.Column(name, sa.Integer(), nullable=False, server_default="0")


def _big(name: str) -> sa.Column:
    return sa.Column(name, sa.BigInteger(), nullable=False, server_default="0")


def upgrade() -> None:
    op.create_table(
        "member_performance_daily",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("member_kind", sa.String(length=16), nullable=False),
        sa.Column(
            "agent_slug", sa.String(length=100), nullable=False, server_default=""
        ),
        sa.Column("team", sa.String(length=50), nullable=True),
        sa.Column("role", sa.String(length=50), nullable=True),
        _int("tasks_completed"),
        _int("tasks_first_pass"),
        _int("revisions_caused"),
        _int("revisions_received"),
        _big("active_runtime_seconds"),
        _int("turns"),
        _int("tool_calls"),
        _big("tokens"),
        sa.Column("cost_usd", sa.Float(), nullable=False, server_default="0"),
        _big("ceo_approval_dwell_seconds"),
        _big("ceo_unblock_dwell_seconds"),
        _int("godmode_actions"),
        # The four CEO-approved extras (+ blocked_seconds).
        _int("qa_reviews_total"),
        _int("qa_reviews_passed"),
        _int("escalations"),
        _int("blocked_others"),
        _big("idle_seconds"),
        _big("blocked_seconds"),
        sa.UniqueConstraint(
            "date", "member_kind", "agent_slug", name="uq_member_perf_day"
        ),
    )
    op.create_index("ix_member_perf_date", "member_performance_daily", ["date"])
    op.create_index(
        "ix_member_perf_agent_slug", "member_performance_daily", ["agent_slug"]
    )
    op.create_index("ix_member_perf_team", "member_performance_daily", ["team"])
    op.create_index("ix_member_perf_kind", "member_performance_daily", ["member_kind"])


def downgrade() -> None:
    op.drop_index("ix_member_perf_kind", table_name="member_performance_daily")
    op.drop_index("ix_member_perf_team", table_name="member_performance_daily")
    op.drop_index("ix_member_perf_agent_slug", table_name="member_performance_daily")
    op.drop_index("ix_member_perf_date", table_name="member_performance_daily")
    op.drop_table("member_performance_daily")
