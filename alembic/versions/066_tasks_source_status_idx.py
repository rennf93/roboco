"""Add ix_tasks_source_status_created — bounded video render-loop scan.

``list_completed_video_tasks`` is bounded by ``.order_by(created_at.desc()
).limit(video_render_scan_limit)``; the composite ``(source, status,
created_at)`` index backs that bounded scan so it doesn't read the growing
completed-history set. Additive; no data change.

Revision ID: 066_tasks_source_status_idx
Revises: 065_rag_index_failures
Create Date: 2026-07-06
"""

from __future__ import annotations

from alembic import op

revision = "066_tasks_source_status_idx"
down_revision = "065_rag_index_failures"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_tasks_source_status_created",
        "tasks",
        ["source", "status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_tasks_source_status_created", table_name="tasks")
