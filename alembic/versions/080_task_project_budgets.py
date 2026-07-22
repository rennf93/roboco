"""Per-task and per-project cost budgets (feature-flagged, default off).

``tasks.budget_usd`` caps one task's own accumulated agent-spawn spend;
``projects.monthly_budget_usd`` caps a project's calendar-month spend across
all its tasks. Both nullable and additive — null means "no cap" and is a pure
no-op regardless of ``ROBOCO_TASK_BUDGETS_ENABLED`` (the flag itself gates
whether the caps are ever consulted at all). Mirrors the ``ci_watch_enabled``
per-project opt-in shape (migration 048): the column exists unconditionally,
the feature flag decides whether anything reads it.

``ix_agent_spawn_sessions_task_id`` rides along: both the claim-time monthly-
spend query (``TaskService.project_month_spend_usd``) and the orchestrator's
per-tick task-spend sweep (``_task_spend_usd``) filter this table by
``task_id`` with no existing index — an unbounded per-row scan every minute
once the flag is armed. Added here rather than a separate migration since it
exists to serve these same two new read paths.

Revision ID: 080_task_project_budgets
Revises: 079_notification_backoff
Create Date: 2026-07-22

NOTE: down_revision was re-chained from 078_project_codegen_command to
079_notification_backoff (PR #652 landed first at integration) — the
079_task_project_budgets -> 080_task_project_budgets rename + re-chain is
exactly the "may be re-chained at integration" case flagged in the original
docstring, not a fork of the tree.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "080_task_project_budgets"
down_revision = "079_notification_backoff"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("budget_usd", sa.Float(), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("monthly_budget_usd", sa.Float(), nullable=True),
    )
    op.create_index(
        "ix_agent_spawn_sessions_task_id",
        "agent_spawn_sessions",
        ["task_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_spawn_sessions_task_id", table_name="agent_spawn_sessions")
    op.drop_column("projects", "monthly_budget_usd")
    op.drop_column("tasks", "budget_usd")
