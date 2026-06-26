"""Add the task_cell_projects table — ad-hoc per-cell project map for a task.

A MegaTask root-subtask that spans multiple cells (and may mix per-cell projects
from different products / OSS libs) needs a per-cell routing map without standing
up a Product for it. ``task_cell_projects`` mirrors ``product_projects`` but is
owned by the task: one Project per cell per task (``UNIQUE (task_id, team)``). The
root-subtask then cuts ``feature/main_pm/{root}`` per repo and opens a root->master
PR per repo exactly like a Product fan-out root — only the map's source differs.
``team`` reuses the existing Postgres "team" enum (create_type=False).

Revision ID: 052_task_cell_projects
Revises: 051_respawn_tracker
Create Date: 2026-06-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "052_task_cell_projects"
down_revision = "051_respawn_tracker"
branch_labels = None
depends_on = None

# Reuse the existing Postgres "team" enum in place (created in 001_initial_schema,
# widened since by later migrations). ``create_type=False`` MUST be set on the
# postgres-native ``postgresql.ENUM``: it's that class's ``create_type`` attribute
# that ``_check_for_name_in_memos`` reads to suppress the redundant ``CREATE TYPE``
# on ``op.create_table`` (checkfirst=False, so the has_type probe is skipped). On
# the generic ``sa.Enum`` the kwarg is silently dropped, so the CREATE TYPE fires
# and crashes a real orchestrator boot ("type 'team' already exists").
_TEAM_ENUM = postgresql.ENUM(
    "backend",
    "frontend",
    "ux_ui",
    "board",
    "main_pm",
    "fullstack",
    "marketing",
    "system",
    name="team",
    create_type=False,
)


def upgrade() -> None:
    op.create_table(
        "task_cell_projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "task_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("team", _TEAM_ENUM, nullable=False),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("projects.id", ondelete="RESTRICT"),
            nullable=False,
            index=True,
        ),
        sa.UniqueConstraint("task_id", "team", name="uq_task_cell_projects_task_team"),
    )


def downgrade() -> None:
    op.drop_table("task_cell_projects")
