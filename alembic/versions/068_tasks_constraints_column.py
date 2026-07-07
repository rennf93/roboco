"""Add tasks.constraints — move the conventions dump out of description.

The 2026-07-07 task-quality defect: ``TaskService._attach_baseline_constraints``
appended a ``## Constraints`` block to ``tasks.description``, so descriptions
ran 3000-4250 chars dominated by an auto-attached conventions dump. The
conventions already reach the agent independently at spawn via
``ConventionsService.render_ambient_block`` -> ``compose_prompt(ambient=...)``,
so the stored block is redundant bloat. Move it to a dedicated nullable
``constraints`` Text column so ``description`` is the human-authored
instruction only and the panel can render the constraints as a read-only card.

Additive: nullable column, default NULL. Existing rows keep their bloated
descriptions (a backfill that regex-strips markdown from a Text column is
riskier than the win — new tasks get clean descriptions from B2); no data
change in the migration.

Revision ID: 068_tasks_constraints_column
Revises: 067_respawn_revisit_resets
Create Date: 2026-07-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "068_tasks_constraints_column"
down_revision = "067_respawn_revisit_resets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("constraints", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tasks", "constraints")
