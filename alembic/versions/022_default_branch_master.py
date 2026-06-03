"""Flip the projects.default_branch server-side default from 'main' to 'master'.

The repositories this system drives use ``master`` as their primary branch, but
the column's default was inherited from the ``main`` convention. Existing rows
already hold ``master``, so this only changes the default applied to future
inserts that omit the column.

Revision ID: 022_default_branch_master
Revises: 021_task_board_review_complete
Create Date: 2026-06-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "022_default_branch_master"
down_revision = "021_task_board_review_complete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "projects",
        "default_branch",
        existing_type=sa.String(length=100),
        existing_nullable=False,
        server_default="master",
    )


def downgrade() -> None:
    op.alter_column(
        "projects",
        "default_branch",
        existing_type=sa.String(length=100),
        existing_nullable=False,
        server_default="main",
    )
