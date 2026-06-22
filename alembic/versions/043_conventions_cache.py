"""Add the project_conventions_cache table.

Caches the parsed *effective* architectural-conventions map per
``(project_id, commit_sha)`` so the map is re-derived only when HEAD moves.
``status`` records how the repo's ``.roboco/conventions.yml`` resolved at that
SHA (``ok`` | ``degraded`` | ``missing``). Pure schema change; no backfill.
Inert until ``ROBOCO_CONVENTIONS_ENABLED``.

Revision ID: 043_conventions_cache
Revises: 042_worksession_toolchain
Create Date: 2026-06-22

NOTE: revision id is 21 chars — alembic's ``alembic_version.version_num`` is
``VARCHAR(32)`` and a longer id raises at record time.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "043_conventions_cache"
down_revision = "042_worksession_toolchain"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_conventions_cache",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("commit_sha", sa.String(length=40), nullable=False),
        sa.Column("effective_map", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("derived_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "commit_sha", name="uq_project_conventions_cache_sha"
        ),
    )
    op.create_index(
        "ix_project_conventions_cache_project_id",
        "project_conventions_cache",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_project_conventions_cache_project_id",
        table_name="project_conventions_cache",
    )
    op.drop_table("project_conventions_cache")
