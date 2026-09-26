"""Add per-question outcome labels to decision_log (Decisions service).

Batched pilots whose questions EACH carry their own truth (findings_mapping's
per-finding "this diff addresses finding F" noul) cannot be graded by one
row-level outcome slug: ``question_outcomes`` stores the per-question fate
map ({"finding_0": "resolved", ...}) that the outcome registry's fate map
resolves to per-question golds in the training exporter. Purely
additive/nullable.

Revision ID: 107_decisionlog_qoutcomes
Revises: 106_decisionlog_corpus
Create Date: 2026-09-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "107_decisionlog_qoutcomes"
down_revision = "106_decisionlog_corpus"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "decision_log", sa.Column("question_outcomes", sa.JSON(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("decision_log", "question_outcomes")
