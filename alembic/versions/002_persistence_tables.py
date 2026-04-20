"""Persistence tables + NotificationType.APPROVAL.

Revision ID: 002_persistence_tables
Revises: 001_initial_schema
Create Date: 2026-04-19

Adds:
- `waiting_records`: durable storage for agents in WAITING_LONG state so
  orchestrator restarts don't strand them (previously in-memory dict).
- `audit_log`: queryable audit trail so the auditor role actually has data
  to inspect (previously log-only).
- `notificationtype` enum value `APPROVAL`: the orchestrator's approval
  dispatcher and the frontend both reference this type, but it was missing
  from the enum, so every approval-related insert raised
  `invalid input value for enum notificationtype: "APPROVAL"`.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "002_persistence_tables"
down_revision = "001_initial_schema"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    """True if `name` already exists in the current DB schema.

    Guards against re-running this migration on DBs that were first created
    via Base.metadata.create_all (which pre-created waiting_records and
    audit_log from the ORM metadata). Without this guard, op.create_table
    raises DuplicateTableError and the whole migration rolls back — so the
    ALTER TYPE ADD VALUE 'APPROVAL' above it never takes effect either.
    """
    bind = op.get_bind()
    return inspect(bind).has_table(name)


def upgrade() -> None:
    # Add the missing APPROVAL value to the notificationtype enum.
    # SQLAlchemy's default Enum(PyEnum) binds enum members by NAME (uppercase),
    # and the live DB was created via Base.metadata.create_all, so the enum
    # values on disk are the uppercase NAMES ('TASK_ASSIGNMENT', 'ALERT', ...).
    # IF NOT EXISTS makes this idempotent on re-runs.
    op.execute(
        "ALTER TYPE notificationtype ADD VALUE IF NOT EXISTS 'APPROVAL'"
    )

    if not _table_exists("waiting_records"):
        op.create_table(
            "waiting_records",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "agent_id",
                sa.String(64),
                nullable=False,
                unique=True,
                index=True,
                comment="Agent slug; unique so only one record per agent.",
            ),
            sa.Column(
                "task_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("tasks.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "waiting_for",
                sa.String(64),
                nullable=False,
                comment="One of: blocker_resolution, qa_result, answer, assignment",
            ),
            sa.Column(
                "waiting_since",
                sa.DateTime(timezone=True),
                nullable=False,
            ),
            sa.Column(
                "context", postgresql.JSONB, nullable=False, server_default="{}"
            ),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
            ),
        )
        op.create_index(
            "ix_waiting_records_waiting_for",
            "waiting_records",
            ["waiting_for"],
        )

    if not _table_exists("audit_log"):
        op.create_table(
            "audit_log",
            sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
            sa.Column(
                "event_type",
                sa.String(80),
                nullable=False,
                index=True,
                comment="Dot-separated e.g. task.claimed, session.closed, project.deleted",
            ),
            sa.Column(
                "agent_id",
                postgresql.UUID(as_uuid=True),
                sa.ForeignKey("agents.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column(
                "target_type",
                sa.String(40),
                nullable=True,
                comment="e.g. task, session, project, notification",
            ),
            sa.Column("target_id", postgresql.UUID(as_uuid=True), nullable=True),
            sa.Column(
                "severity",
                sa.String(16),
                nullable=False,
                server_default="info",
                comment="info | warning | error",
            ),
            sa.Column(
                "details", postgresql.JSONB, nullable=False, server_default="{}"
            ),
            sa.Column(
                "timestamp",
                sa.DateTime(timezone=True),
                server_default=sa.func.now(),
                nullable=False,
                index=True,
            ),
        )
        op.create_index(
            "ix_audit_log_agent_timestamp",
            "audit_log",
            ["agent_id", "timestamp"],
        )
        op.create_index(
            "ix_audit_log_target", "audit_log", ["target_type", "target_id"]
        )


def downgrade() -> None:
    op.drop_index("ix_audit_log_target", table_name="audit_log")
    op.drop_index("ix_audit_log_agent_timestamp", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_index("ix_waiting_records_waiting_for", table_name="waiting_records")
    op.drop_table("waiting_records")
