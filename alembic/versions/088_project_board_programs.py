"""Add projects.board_programs — per-project Board Program scoping.

A project-scoped program key ("pest_control") opts a project INTO that
program's cycles (affirmative opt-in, null = out). An org-scoped key
prefixed "!" ("!roadmap") opts a project OUT of that program's output
(default-eligible, null = in). See
``roboco.foundation.policy.board_programs.project_participates``. Additive,
nullable; inert until a project sets it or a project-scoped program lands.

Revision ID: 088_project_board_programs
Revises: 087_board_program_cycles
Create Date: 2026-07-24
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "088_project_board_programs"
down_revision = "087_board_program_cycles"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("board_programs", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "board_programs")
