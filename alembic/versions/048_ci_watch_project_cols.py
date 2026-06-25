"""Per-project multi-repo CI-watch opt-in columns.

Multi-repo CI-watch generalizes the single-repo self-heal CI loop to any
project the operator opts in. A project is watched only when
``ci_watch_enabled`` is set; ``ci_watch_workflow`` scopes the CI signal to one
workflow file (null → the engine's configured default). Both are additive and
default-off, so existing projects keep today's behavior (unwatched).

Revision ID: 048_ci_watch_project_cols
Revises: 047_ws_single_active
Create Date: 2026-06-25

NOTE: revision id is 25 chars — alembic's ``alembic_version.version_num`` is
``VARCHAR(32)`` and a longer id raises at record time.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "048_ci_watch_project_cols"
down_revision = "047_ws_single_active"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "ci_watch_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "projects",
        sa.Column("ci_watch_workflow", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "ci_watch_workflow")
    op.drop_column("projects", "ci_watch_enabled")
