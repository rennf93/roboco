"""Alembic migration: the decision_log table (Decisions service).

The Auditor's daily decisions-audit review needs persisted verdict history
(baseline, calibration evidence). This is the spec section 4 "decision_log
table in a dedicated migration", pulled forward by CEO direction. Purely
additive; nothing existing reads or writes it.

Revision ID: 105_decisionlog
Revises: 104_a2a_steering
Create Date: 2026-09-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "105_decisionlog"
down_revision = "104_a2a_steering"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "decision_log",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("pilot", sa.String(60), nullable=False),
        sa.Column("tier", sa.String(20), nullable=True),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("session_id", sa.String(280), nullable=True),
        sa.Column("answers", sa.JSON(), nullable=True),
        sa.Column("confidence", sa.JSON(), nullable=True),
        sa.Column("action", sa.String(160), nullable=True),
        sa.Column("cost", sa.Float(), nullable=True),
    )
    op.create_index("ix_decision_log_created_at", "decision_log", ["created_at"])
    op.create_index(
        "ix_decision_log_pilot_created", "decision_log", ["pilot", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_decision_log_pilot_created", table_name="decision_log")
    op.drop_index("ix_decision_log_created_at", table_name="decision_log")
    op.drop_table("decision_log")
