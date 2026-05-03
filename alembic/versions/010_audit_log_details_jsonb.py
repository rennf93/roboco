"""audit_log.details JSON -> JSONB so .astext works in ORM queries.

Migration 002 originally created `audit_log.details` as `postgresql.JSONB`
when the alembic chain was applied cleanly, BUT the ORM in
`roboco/db/tables.py` declared the column as generic `JSON`. Two failure
modes followed:

1. DBs bootstrapped via `Base.metadata.create_all` (the production
   create_all-fallback path in `roboco.db.base.init_db`, and the test
   conftest path) created the column as `JSON` — not JSONB. PostgreSQL
   stores `JSON` as text and exposes a different operator class than
   `JSONB`, so `details->>'reason'` works at the SQL level but
   SQLAlchemy's ORM generates the `JSON.Comparator` (no `.astext`)
   instead of the `JSONB.Comparator` (which has `.astext`).

2. Even DBs where the alembic-created column IS JSONB at the storage
   layer still hit the same ORM-side failure, because SQLAlchemy picks
   the comparator from the column TYPE the ORM declares — not what's on
   disk.

`AuditService.has_recent_tracing_gap` filters
`details->>'reason' == 'tracing_gap'` for the PM-respawn circuit
breaker. With the ORM declaring `JSON`, the `.astext` access raises
`AttributeError` at query construction time. The choreographer's
exception handler in `_pm_made_rule_following_retry` swallows it and
returns False, leaving the strike-count reset (Task 13 887d073)
permanently inert.

This migration converts the column to JSONB at the storage layer
(idempotent on already-JSONB DBs because PG accepts a JSONB->JSONB
ALTER as a no-op cast). The ORM is updated in the same commit to
declare `JSONB`, which is the change that actually unblocks `.astext`.

Revision ID: 010_audit_log_details_jsonb
Revises: 009_enum_reconcile
Create Date: 2026-05-03
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "010_audit_log_details_jsonb"
down_revision = "009_enum_reconcile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Convert audit_log.details from JSON to JSONB.

    PG can convert JSON -> JSONB in place because they're text-compatible.
    `USING details::jsonb` re-parses every existing row as JSONB. The
    server_default is updated from `'{}'::json` to `'{}'::jsonb` to match
    the new column type — leaving it as `'{}'::json` would cause an
    implicit cast on every INSERT.

    Idempotent on a database where the column is already JSONB: the
    `details::jsonb` cast on a JSONB value is a no-op, the type change
    is a no-op, and the default is reset to the same value.
    """
    op.alter_column(
        "audit_log",
        "details",
        existing_type=sa.JSON(),
        type_=postgresql.JSONB(),
        existing_nullable=False,
        existing_server_default=sa.text("'{}'::json"),
        server_default=sa.text("'{}'::jsonb"),
        postgresql_using="details::jsonb",
    )


def downgrade() -> None:
    """Convert audit_log.details back from JSONB to JSON.

    JSONB -> JSON is also a safe cast (JSONB serializes back to text).
    The default is reset to the original `'{}'::json` shape. Note: the
    ORM-side `JSONB` declaration must be reverted alongside this
    downgrade, or `.astext` queries will start raising AttributeError
    again at runtime.
    """
    op.alter_column(
        "audit_log",
        "details",
        existing_type=postgresql.JSONB(),
        type_=sa.JSON(),
        existing_nullable=False,
        existing_server_default=sa.text("'{}'::jsonb"),
        server_default=sa.text("'{}'::json"),
        postgresql_using="details::json",
    )
