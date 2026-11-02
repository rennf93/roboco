"""Entire trails bridge: credentials, trail/change/finding mappings, outbox,
and the per-project opt-in column.

``projects.entire_trails_enabled`` opts a GitHub project's task trees into
the bridge (default off). ``entire_credentials`` holds the encrypted Entire
token. ``entire_trails`` / ``entire_trail_changes`` /
``entire_trail_findings`` map RoboCo rows to Entire's, and
``entire_trail_outbox`` carries the operations written with the RoboCo
change they mirror. Purely additive.

Revision ID: 110_entire_trails
Revises: 109_memory_links
Create Date: 2026-10-29
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "110_entire_trails"
down_revision = "109_memory_links"
branch_labels = None
depends_on = None


def _timestamp(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=None if nullable else sa.text("now()"),
    )


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column(
            "entire_trails_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.create_table(
        "entire_credentials",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("token_encrypted", sa.Text(), nullable=True),
        _timestamp("created_at"),
        _timestamp("updated_at", nullable=True),
    )
    op.create_table(
        "entire_trails",
        sa.Column(
            "root_task_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("tasks.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("entire_project", sa.String(200), nullable=False),
        sa.Column(
            "create_fields",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("trail_id", sa.String(64), nullable=True),
        sa.Column("trail_number", sa.Integer(), nullable=True),
        sa.Column("trail_url", sa.String(500), nullable=True),
        _timestamp("created_at"),
        _timestamp("synced_at", nullable=True),
        _timestamp("closed_at", nullable=True),
    )
    op.create_table(
        "entire_trail_changes",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "root_task_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("entire_trails.root_task_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("task_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("repository", sa.String(300), nullable=False),
        sa.Column("branch", sa.String(500), nullable=False),
        sa.Column("base", sa.String(500), nullable=False),
        sa.Column("change_number", sa.Integer(), nullable=True),
        _timestamp("linked_at"),
        sa.UniqueConstraint(
            "repository", "branch", name="uq_entire_trail_changes_repo_branch"
        ),
    )
    op.create_index(
        "ix_entire_trail_changes_root_task_id",
        "entire_trail_changes",
        ["root_task_id"],
    )
    op.create_table(
        "entire_trail_findings",
        sa.Column(
            "review_finding_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("task_review_findings.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("entire_finding_id", sa.String(64), nullable=False),
        sa.Column("repository", sa.String(300), nullable=False),
        sa.Column("branch", sa.String(500), nullable=False),
        _timestamp("created_at"),
    )
    op.create_table(
        "entire_trail_outbox",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "root_task_id",
            sa.UUID(as_uuid=True),
            sa.ForeignKey("entire_trails.root_task_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("op", sa.String(32), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("dead", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_error", sa.Text(), nullable=True),
        _timestamp("created_at"),
        _timestamp("next_attempt_at"),
    )
    op.create_index(
        "ix_entire_trail_outbox_root_task_id",
        "entire_trail_outbox",
        ["root_task_id"],
    )
    op.create_index(
        "ix_entire_trail_outbox_next_attempt_at",
        "entire_trail_outbox",
        ["next_attempt_at"],
    )


def downgrade() -> None:
    op.drop_table("entire_trail_outbox")
    op.drop_table("entire_trail_findings")
    op.drop_table("entire_trail_changes")
    op.drop_table("entire_trails")
    op.drop_table("entire_credentials")
    op.drop_column("projects", "entire_trails_enabled")
