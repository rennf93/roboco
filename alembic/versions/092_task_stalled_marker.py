"""Durable stalled/needs-human marker on tasks.

``tasks.stalled_reason`` + ``tasks.stalled_since`` record a give-up decision
the dispatcher's respawn breaker (``_pm_respawn_should_gate`` in
``roboco/runtime/orchestrator.py``) makes on a task's behalf — previously
only a log line + a one-shot CEO notification that ages out of the bell,
with nothing durable on the task itself. Both nullable/additive: a null
``stalled_reason`` means "never stalled or since cleared by genuine forward
progress" and is a pure no-op for every existing row and caller.

``stalled_reason`` is a plain ``String(50)``, not a DB enum — see
``roboco.models.base.StalledReason`` — so a future reason value (e.g. the
no-task_id notification-cap path) needs no ``ALTER TYPE`` migration. The
partial index only covers actually-stalled rows (the common case is NULL),
backing the read endpoint's "current stalled set" query.

Revision ID: 092_task_stalled_marker
Revises: 091_seed_kimi_provider
Create Date: 2026-08-08
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "092_task_stalled_marker"
down_revision = "091_seed_kimi_provider"
branch_labels: dict[str, str] | None = None
depends_on: dict[str, str] | None = None

_INDEX = "ix_tasks_stalled_reason"


def upgrade() -> None:
    op.add_column("tasks", sa.Column("stalled_reason", sa.String(50), nullable=True))
    op.add_column(
        "tasks",
        sa.Column("stalled_since", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        _INDEX,
        "tasks",
        ["stalled_reason"],
        postgresql_where=sa.text("stalled_reason IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="tasks")
    op.drop_column("tasks", "stalled_since")
    op.drop_column("tasks", "stalled_reason")
