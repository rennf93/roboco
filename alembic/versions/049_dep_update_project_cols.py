"""Per-project dependency-update bot opt-in columns.

The dependency-update bot participates for a project only when
``dep_update_command`` is set (e.g. ``uv lock --upgrade`` / ``pnpm update``);
``dep_update_paths`` are the lockfile globs the probe inspects (null → infer
``uv.lock`` / ``pnpm-lock.yaml``). Both are additive and nullable, so existing
projects keep today's behavior (not participating).

Revision ID: 049_dep_update_project_cols
Revises: 048_ci_watch_project_cols
Create Date: 2026-06-25

NOTE: revision id is 27 chars — alembic's ``alembic_version.version_num`` is
``VARCHAR(32)`` and a longer id raises at record time.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "049_dep_update_project_cols"
down_revision = "048_ci_watch_project_cols"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("dep_update_command", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("dep_update_paths", sa.ARRAY(sa.String()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "dep_update_paths")
    op.drop_column("projects", "dep_update_command")
