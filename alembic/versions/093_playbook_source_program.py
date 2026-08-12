"""Add playbooks.source_program — the Board Program that drafted this playbook.

Discriminates a Coroner-authored playbook from a Librarian-authored one: both
draft directly via PlaybookService.draft() with created_by stamped to the
same fixed Auditor identity (see PlaybookService's module docstring), so
created_by alone can never tell them apart — the live bug this column fixes
(a Coroner playbook decision was recorded against the "librarian" LEARN
cycle). Nullable/additive: None means "not a board-program cycle item" (an
ordinary delivery-role draft_playbook draft), which is how every existing
row reads once this column exists.

Revision ID: 093_playbook_source_program
Revises: 092_task_stalled_marker
Create Date: 2026-08-12
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "093_playbook_source_program"
down_revision = "092_task_stalled_marker"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None


def upgrade() -> None:
    op.add_column(
        "playbooks", sa.Column("source_program", sa.String(length=30), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("playbooks", "source_program")
