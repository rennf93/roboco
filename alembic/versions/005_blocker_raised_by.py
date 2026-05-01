"""Add blocker_raised_by to tasks.

Revision ID: 005_blocker_raised_by
Revises: 004_provider_routing
Create Date: 2026-04-22

Adds `tasks.blocker_raised_by` so `roboco_task_unblock` can restore the
task to the agent who actually raised the block/escalation. Without this,
escalations (which reassign the task to the escalation target for
resolution) leave the dev's identity lost — unblocking just flips status
back to `in_progress` with the PM still on the hook, so the orchestrator
never respawns the original dev and the task stalls.

NULL = never blocked, or legacy rows pre-migration. `unblock` treats NULL
as "no-op on assignee" to preserve back-compat.
"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "005_blocker_raised_by"
down_revision = "004_provider_routing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "blocker_raised_by",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "blocker_raised_by")
