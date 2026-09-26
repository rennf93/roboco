"""Add training-corpus columns to decision_log (Decisions service).

Every decision_log row now carries the question inputs EXACTLY as sent on
the wire (``state`` / ``questions``, spec 3.1 caps already applied by the
client) plus the ground-truth label an outcome producer attaches after the
fact (``outcome`` / ``outcome_at``). One labeled row is one fine-tunable
example for the Laya checkpoint; unlabeled rows stay corpus candidates for
bulk labeling. Purely additive/nullable: a no-op for every existing row
and caller. Adds ``ix_decision_log_pilot_session`` so producers can address
rows by the deterministic per-subject session ids the pilots compose.

Revision ID: 106_decisionlog_corpus
Revises: 105_decisionlog
Create Date: 2026-09-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "106_decisionlog_corpus"
down_revision = "105_decisionlog"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("decision_log", sa.Column("state", sa.JSON(), nullable=True))
    op.add_column("decision_log", sa.Column("questions", sa.JSON(), nullable=True))
    op.add_column(
        "decision_log", sa.Column("outcome", sa.String(60), nullable=True)
    )
    op.add_column(
        "decision_log",
        sa.Column("outcome_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_decision_log_pilot_session", "decision_log", ["pilot", "session_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_decision_log_pilot_session", table_name="decision_log")
    op.drop_column("decision_log", "outcome_at")
    op.drop_column("decision_log", "outcome")
    op.drop_column("decision_log", "questions")
    op.drop_column("decision_log", "state")
