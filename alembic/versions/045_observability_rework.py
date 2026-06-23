"""Observability rework tracking: tasks.revision_count + audit_log query index.

``tasks.revision_count`` makes the per-task rework rate an O(1) column read
instead of an audit_log scan; the composite index on
``audit_log(target_id, event_type, timestamp)`` keeps the cycle-time and rework
reconstruction queries fast. Pure schema change, no backfill — existing rows
default to 0 (the counter is forward-only, matching the design's forward-only
rework attribution).

Revision ID: 045_observability_rework
Revises: 044_convention_findings
Create Date: 2026-06-23

NOTE: revision id is 24 chars — alembic's ``alembic_version.version_num`` is
``VARCHAR(32)`` and a longer id raises at record time.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "045_observability_rework"
down_revision = "044_convention_findings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "revision_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.create_index(
        "ix_audit_log_target_event_ts",
        "audit_log",
        ["target_id", "event_type", "timestamp"],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_log_target_event_ts", table_name="audit_log")
    op.drop_column("tasks", "revision_count")
