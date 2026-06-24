"""Enforce one ACTIVE work session per task.

A task is owned by exactly one agent at a time, so it must carry at most one
ACTIVE ``work_sessions`` row. Nothing enforced this: ``create`` only blocked a
duplicate for the *same* agent, so a re-claim by a different agent (pool
release, reaper unclaim, escalation redirect) left the prior holder's ACTIVE
session open. ``WorkSessionService.get_active_for_task`` then ran
``scalar_one_or_none()`` over the duplicate rows and raised
``MultipleResultsFound``; the caught failure left a ``None`` that the verb flow
dereferenced as ``'NoneType' object has no attribute 'id'`` — wedging
``i_will_plan`` into an infinite PM respawn loop.

This migration (1) deduplicates existing rows — keeping the most recent ACTIVE
session per task and abandoning the rest — then (2) adds a partial unique index
so the invariant can never be violated again. The service layer now also
supersedes stale sessions on claim; this is the DB backstop.

Revision ID: 047_ws_single_active
Revises: 046_batch_intake
Create Date: 2026-06-24

NOTE: revision id is 20 chars — alembic's ``alembic_version.version_num`` is
``VARCHAR(32)`` and a longer id raises at record time.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "047_ws_single_active"
down_revision = "046_batch_intake"
branch_labels = None
depends_on = None

_INDEX = "uq_work_sessions_one_active_per_task"


def upgrade() -> None:
    # 1) Deduplicate: for every task with >1 ACTIVE session keep the most recent
    #    (latest started_at, id as deterministic tie-break) and abandon the rest.
    op.execute(
        sa.text(
            """
            UPDATE work_sessions ws
            SET status = 'abandoned',
                ended_at = COALESCE(ws.ended_at, now())
            WHERE ws.status = 'active'
              AND ws.id <> (
                SELECT keep.id
                FROM work_sessions keep
                WHERE keep.task_id = ws.task_id
                  AND keep.status = 'active'
                ORDER BY keep.started_at DESC, keep.id DESC
                LIMIT 1
              )
            """
        )
    )
    # 2) Enforce the invariant at the DB level: at most one ACTIVE row per task.
    op.create_index(
        _INDEX,
        "work_sessions",
        ["task_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="work_sessions")
