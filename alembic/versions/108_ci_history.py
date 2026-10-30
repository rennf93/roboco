"""Add CI run history + memory retrieval log (Decisions training corpus).

``ci_runs`` persists every distinct CI reading the pull-based telemetry
consumers observe (project, workflow, conclusion, the run's own
completion stamp) - the run history the outcome labeler needs to grade
ci_watch_route and dep_update_risk. ``memory_retrieval_log`` records
which institutional-memory items were injected into which spawn - the
foundation for grading the knowledge-family pilots. Purely additive; no
existing reader or writer changes meaning.

Revision ID: 108_ci_history
Revises: 107_decisionlog_qoutcomes
Create Date: 2026-09-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "108_ci_history"
down_revision = "107_decisionlog_qoutcomes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ci_runs",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("project_slug", sa.String(200), nullable=False),
        sa.Column("workflow", sa.String(200), nullable=True),
        sa.Column("conclusion", sa.String(40), nullable=False),
        sa.Column("is_breach", sa.Boolean(), nullable=False),
        sa.Column("observed_at_str", sa.String(80), nullable=False),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_ci_runs_created_at", "ci_runs", ["created_at"]
    )
    op.create_index(
        "ix_ci_runs_project_created", "ci_runs", ["project_slug", "created_at"]
    )
    op.create_unique_constraint(
        "ux_ci_runs_reading",
        "ci_runs",
        ["project_slug", "workflow", "observed_at_str"],
    )
    op.create_table(
        "memory_retrieval_log",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("source", sa.String(500), nullable=False),
        sa.Column("task_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("agent_slug", sa.String(64), nullable=True),
        sa.Column(
            "retrieved_at", sa.DateTime(timezone=True), nullable=False
        ),
    )
    op.create_index(
        "ix_memory_retrieval_created_at",
        "memory_retrieval_log",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_table("memory_retrieval_log")
    op.drop_constraint("ux_ci_runs_reading", "ci_runs", type_="unique")
    op.drop_index("ix_ci_runs_project_created", table_name="ci_runs")
    op.drop_index("ix_ci_runs_created_at", table_name="ci_runs")
    op.drop_table("ci_runs")
