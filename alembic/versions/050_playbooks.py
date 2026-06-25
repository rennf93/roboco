"""Add the playbooks table — curated, Auditor-approved reusable procedures.

A playbook records "here is how to do X" (vs a learning's "this happened"). An
agent drafts one (status=draft); the Auditor approves it (status=approved) and
only then is it embedded into the PLAYBOOKS RAG index. Orthogonal to the task
lifecycle. Status is a plain String column (the PlaybookStatus StrEnum carries
the valid values at the service layer), matching the pitches convention — so no
DB enum type and no enum-parity migration are needed.

Revision ID: 050_playbooks
Revises: 049_dep_update_project_cols
Create Date: 2026-06-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "050_playbooks"
down_revision = "049_dep_update_project_cols"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "playbooks",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("problem", sa.Text(), nullable=False),
        sa.Column("procedure", sa.Text(), nullable=False),
        sa.Column(
            "tags", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")
        ),
        sa.Column("team", sa.String(length=20), nullable=True),
        sa.Column(
            "scope", sa.String(length=20), nullable=False, server_default="org"
        ),
        sa.Column(
            "source_task_ids",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'::json"),
        ),
        sa.Column(
            "status", sa.String(length=20), nullable=False, server_default="draft"
        ),
        sa.Column("created_by", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("approved_by", sa.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_playbooks_slug", "playbooks", ["slug"], unique=True)
    op.create_index("ix_playbooks_status", "playbooks", ["status"])


def downgrade() -> None:
    op.drop_index("ix_playbooks_status", table_name="playbooks")
    op.drop_index("ix_playbooks_slug", table_name="playbooks")
    op.drop_table("playbooks")
