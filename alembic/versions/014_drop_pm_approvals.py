"""Drop unused Task column pm_approvals.

Smoke run analysis (2026-05-12) initially flagged three Task fields
as unused: pm_approvals, quick_context, proactive_context. A follow-up
audit found quick_context and proactive_context are actively written
(original_developer marker, doc_notes, PR creator tags; RAG context
injection respectively). Only pm_approvals is truly orphaned — zero
writers, only model + schema declarations as readers.

Revision ID: 014_drop_pm_approvals
Revises: 013_drop_role_enum
Create Date: 2026-05-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "014_drop_pm_approvals"
down_revision = "013_drop_role_enum"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("tasks", "pm_approvals")


def downgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("pm_approvals", sa.JSON, nullable=False, server_default="{}"),
    )
