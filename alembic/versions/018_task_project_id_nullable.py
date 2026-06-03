"""Make tasks.project_id nullable (board/fan-out tasks carry product_id instead).

A task that fans out across cells via a Product has no single repo of its own —
its cell subtasks each resolve a real project from the Product's cell->project
map. So project_id is no longer required at the DB level; a task must have
project_id OR product_id, enforced in the TaskCreate schema (a code task targets
a repo; a board/coordination task carries a product and does no git itself).

Revision ID: 018_task_project_id_nullable
Revises: 017_reconcile_orm_schema_drift
Create Date: 2026-06-02
"""

from __future__ import annotations

from alembic import op
from sqlalchemy.dialects import postgresql

revision = "018_task_project_id_nullable"
down_revision = "017_reconcile_orm_schema_drift"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "tasks",
        "project_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )


def downgrade() -> None:
    # Reverting requires every task to have a project_id; a fan-out task created
    # with only a product_id would block this (acceptable for a downgrade).
    op.alter_column(
        "tasks",
        "project_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
