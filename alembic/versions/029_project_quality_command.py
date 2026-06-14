"""Add projects.quality_command — the fast pre-submit gate command.

When set, the agent pre-submit gate (run at a developer's i_am_done) executes
this command in the developer's workspace — lint + types + complexity, no tests
(e.g. "make gate") — instead of the lint/typecheck pair, so a red gate is caught
at the dev's desk. Nullable; projects opt in via the panel.

Revision ID: 029_project_quality_command
Revises: 028_seed_self_hosted_provider
Create Date: 2026-06-14
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "029_project_quality_command"
down_revision = "028_seed_self_hosted_provider"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("quality_command", sa.String(length=500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("projects", "quality_command")
