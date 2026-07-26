"""Add board_program_cycles.nothing_to_propose_reason.

The ``nothing_to_propose`` do-verb — a Board Program explorer's explicit
"this cycle found nothing worth proposing" exit, distinct from the
per-item-decision ``decisions`` column — needs somewhere to park its reason
so ``BoardProgramEngine._render_cycle`` can surface WHY a cycle proposed
zero items in the next cycle's LEARN context, instead of a bare "proposed 0,
approved 0". Nullable; unset for every pre-existing row.

Revision ID: 089_board_cycle_ntp_reason
Revises: 088_project_board_programs
Create Date: 2026-07-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "089_board_cycle_ntp_reason"
down_revision = "088_project_board_programs"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None


def upgrade() -> None:
    op.add_column(
        "board_program_cycles",
        sa.Column("nothing_to_propose_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("board_program_cycles", "nothing_to_propose_reason")
