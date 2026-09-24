"""Add the `steering` column to a2a_messages (Decisions steer gate).

The steer gate (docs/internal/decisions-spec.md section 6.6) classifies
HOW a peer DM should reach its recipient. Messages the gate marks
`steer_switch_consideration` or `steer_now` carry that mode in this column
and are rendered into the recipient's NEXT context boundary (spawn
briefing or live turn queue) instead of waiting to be noticed. NULL means
ordinary pull-only delivery: every pre-Decisions row and every
queue_after_current / fyi_pull verdict. Purely additive/nullable: a no-op
for every existing row and caller.

Revision ID: 104_a2a_steering
Revises: 103_agentrole_devops
Create Date: 2026-09-23
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "104_a2a_steering"
down_revision = "103_agentrole_devops"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "a2a_messages",
        sa.Column("steering", sa.String(50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("a2a_messages", "steering")
