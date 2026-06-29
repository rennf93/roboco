"""Add archived_by / archived_at to playbooks — distinct retirement attribution.

``archive`` (retire an APPROVED playbook) and ``reject`` (decline a DRAFT) both
end in ARCHIVED, but they are distinct curation acts from ``approve``. Stamping
the archiver into ``approved_by``/``approved_at`` overwrote the approval
provenance (and fabricated approval attribution for a rejected draft that was
never approved). These two columns record who retired it and when, leaving
``approved_by``/``approved_at`` to record only the approval.

Revision ID: 053_playbook_archived_attr
Revises: 052_task_cell_projects
Create Date: 2026-06-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "053_playbook_archived_attr"
down_revision = "052_task_cell_projects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "playbooks",
        sa.Column("archived_by", sa.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "playbooks",
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("playbooks", "archived_at")
    op.drop_column("playbooks", "archived_by")
