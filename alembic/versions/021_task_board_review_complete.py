"""Add tasks.board_review_complete — the board-review handoff flag.

A board/coordination task (no repo of its own, carries a product) is reviewed
by BOTH the Product Owner and the Head of Marketing before the CEO hands it to
Main PM. The task stays ``pending`` throughout — that pending state is what
drives Main PM dispatch once the CEO approves. The CEO's Approve & Start button
must NOT appear until the board has actually finished reviewing, so we persist a
flag the orchestrator sets once both reviewers are done. Defaults to False;
existing rows backfill to False (no board task has been reviewed retroactively).

Revision ID: 021_task_board_review_complete
Revises: 020_backfill_enum_values
Create Date: 2026-06-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "021_task_board_review_complete"
down_revision = "020_backfill_enum_values"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "board_review_complete",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "board_review_complete")
