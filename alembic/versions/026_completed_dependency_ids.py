"""Add tasks.completed_dependency_ids — remember which dependency cleared.

When an upstream dependency completes, ``_unblock_dependents`` removes its id
from the dependent's ``dependency_ids`` so the dependent can be claimed. That
strips the only record of *which* upstream task just landed, so the revived
dependent agent has no way to know what it can now build on — it re-discovers
the upstream work from cold. This column keeps the cleared dependency ids so the
briefing can surface "dependency X just completed" to the agent picking the
task back up. Defaults to an empty array; existing rows backfill empty (no
historical unblock is reconstructed).

Revision ID: 026_completed_dependency_ids
Revises: 025_agentrole_prompter
Create Date: 2026-06-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, UUID

revision = "026_completed_dependency_ids"
down_revision = "025_agentrole_prompter"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "completed_dependency_ids",
            ARRAY(UUID(as_uuid=True)),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "completed_dependency_ids")
