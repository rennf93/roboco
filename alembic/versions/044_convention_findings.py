"""Add the project_convention_findings table (violations feed).

Persists architectural-conventions validator findings per task so the panel
can show recent block/warn violations across a project. Pure schema change;
no backfill. Inert until ``ROBOCO_CONVENTIONS_ENABLED``.

Revision ID: 044_convention_findings
Revises: 043_conventions_cache
Create Date: 2026-06-22

NOTE: revision id is 23 chars — alembic's ``alembic_version.version_num`` is
``VARCHAR(32)`` and a longer id raises at record time.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "044_convention_findings"
down_revision = "043_conventions_cache"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_convention_findings",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("file", sa.String(length=500), nullable=False),
        sa.Column("line", sa.Integer(), nullable=False),
        sa.Column("rule", sa.String(length=100), nullable=False),
        sa.Column("level", sa.String(length=20), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=True),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_project_convention_findings_project_id",
        "project_convention_findings",
        ["project_id"],
    )
    op.create_index(
        "ix_project_convention_findings_detected_at",
        "project_convention_findings",
        ["detected_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_project_convention_findings_detected_at",
        table_name="project_convention_findings",
    )
    op.drop_index(
        "ix_project_convention_findings_project_id",
        table_name="project_convention_findings",
    )
    op.drop_table("project_convention_findings")
