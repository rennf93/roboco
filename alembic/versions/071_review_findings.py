"""Add task_review_findings (the revision-findings ledger) + tasks.pm_notes.

Persists every qa_fail / pr_fail / request_changes / ceo_reject finding across
every revision round as an append-only ledger — unlike notes_structured (one
snapshot per content type, overwritten in place), a row here is never deleted,
only advanced through open -> addressed -> verified (or waived). tasks.pm_notes
is the new PM merge-review-reject note slot (mirrors pr_reviewer_notes),
finally giving request_changes a structured home instead of a raw dev_notes
append. Pure additive schema change; no backfill.

Revision ID: 071_review_findings
Revises: 070_vault_seen_notes
Create Date: 2026-07-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "071_review_findings"
down_revision = "070_vault_seen_notes"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None


def upgrade() -> None:
    op.add_column("tasks", sa.Column("pm_notes", sa.Text(), nullable=True))

    op.create_table(
        "task_review_findings",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("origin", sa.String(length=20), nullable=False),
        sa.Column("round", sa.Integer(), nullable=False),
        sa.Column("author_slug", sa.String(length=64), nullable=True),
        sa.Column("file", sa.String(length=300), nullable=True),
        sa.Column("line", sa.Integer(), nullable=True),
        sa.Column("severity", sa.String(length=20), nullable=False),
        sa.Column("criterion", sa.String(length=500), nullable=True),
        sa.Column("expected", sa.String(length=300), nullable=False),
        sa.Column("actual", sa.String(length=300), nullable=False),
        sa.Column("fix", sa.String(length=500), nullable=True),
        sa.Column("evidence", sa.String(length=2000), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default="open",
        ),
        sa.Column("addressed_by_commit", sa.String(length=100), nullable=True),
        sa.Column("resolution_note", sa.String(length=300), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["task_id"], ["tasks.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_task_review_findings_task_id",
        "task_review_findings",
        ["task_id"],
    )
    op.create_index(
        "ix_task_review_findings_task_status",
        "task_review_findings",
        ["task_id", "status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_task_review_findings_task_status", table_name="task_review_findings"
    )
    op.drop_index("ix_task_review_findings_task_id", table_name="task_review_findings")
    op.drop_table("task_review_findings")
    op.drop_column("tasks", "pm_notes")
