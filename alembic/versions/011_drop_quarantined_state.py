"""Drop the `quarantined` value from the taskstatus enum (phantom state).

Audit D15. The `quarantined` member was added to the taskstatus enum in
migration 009 alongside the lifecycle-table entry
`"quarantined": ["pending"]` in `roboco/enforcement/task_lifecycle.py`,
on the assumption that some service path would later route problematic
tasks through it. None ever did. No verb, no API route, no orchestrator
sweep, and no service method ever sets `task.status = QUARANTINED` or
reads it. It is a phantom state.

This migration removes the value from the postgres enum. PostgreSQL has
no `ALTER TYPE ... DROP VALUE` primitive, so the only safe path is to
rebuild the type:

  1. Pre-flight: refuse to proceed if any row in any table currently
     holds the value `quarantined`. Per the audit no row should — the
     check is here so an unexpected row stops the migration loudly
     instead of silently being lost during the column-cast step below.
  2. Read every (table, column) that references `taskstatus`.
  3. Rename `taskstatus` -> `taskstatus_old`.
  4. Create the new `taskstatus` type without `quarantined`.
  5. ALTER each column to use the new type with
     `USING column::text::taskstatus`.
  6. Drop `taskstatus_old`.

The downgrade re-adds `quarantined` as a member at the end of the enum
(this is what postgres allows directly with `ALTER TYPE ADD VALUE`).
This restores the schema-level shape so a rollback can re-create the
prior member; it does not restore any rows because none had the value.

Revision ID: 011_drop_quarantined_state
Revises: 010_audit_log_details_jsonb
Create Date: 2026-05-03
"""

from __future__ import annotations

from alembic import context, op
from sqlalchemy import text

revision = "011_drop_quarantined_state"
down_revision = "010_audit_log_details_jsonb"
branch_labels = None
depends_on = None


# Members of the rebuilt taskstatus type, in canonical lifecycle order.
# Matches `roboco.models.base.TaskStatus` minus `quarantined`.
_NEW_TASKSTATUS_MEMBERS: tuple[str, ...] = (
    "backlog",
    "pending",
    "claimed",
    "in_progress",
    "blocked",
    "paused",
    "verifying",
    "needs_revision",
    "awaiting_qa",
    "awaiting_documentation",
    "awaiting_pm_review",
    "awaiting_ceo_approval",
    "completed",
    "cancelled",
)


def upgrade() -> None:
    """Drop `quarantined` from taskstatus by rebuilding the type.

    Skipped in offline (--sql) mode: the rebuild is fully introspective
    (queries pg_attrdef / pg_type / etc.) and has no representable
    offline form. Apply against a live DB.
    """
    if context.is_offline_mode():
        return
    bind = op.get_bind()

    # Step 1: pre-flight. Look up every column that references taskstatus,
    # capture each one's column-default expression (so we can re-apply it
    # after the type swap), and refuse to proceed if any row currently
    # holds `quarantined`. PG won't auto-cast a default expression across
    # a type change, so the type swap below requires DROP DEFAULT first
    # then re-applying the same default text against the new type.
    usages = [
        (row[0], row[1], row[2])
        for row in bind.execute(
            text(
                """
                SELECT n.nspname || '.' || c.relname AS table_qualified,
                       a.attname,
                       pg_get_expr(d.adbin, d.adrelid) AS default_expr
                FROM pg_attribute a
                JOIN pg_class c ON a.attrelid = c.oid
                JOIN pg_namespace n ON c.relnamespace = n.oid
                JOIN pg_type t ON a.atttypid = t.oid
                LEFT JOIN pg_attrdef d
                       ON d.adrelid = a.attrelid
                      AND d.adnum = a.attnum
                WHERE t.typname = 'taskstatus'
                  AND a.attnum > 0
                  AND NOT a.attisdropped
                  AND c.relkind = 'r'
                """
            )
        )
    ]

    for table_qualified, column, _default in usages:
        offending = bind.execute(
            text(
                f"SELECT COUNT(*) FROM {table_qualified} "
                f"WHERE {column}::text = 'quarantined'"
            )
        ).scalar_one()
        if offending:
            raise RuntimeError(
                f"Refusing to drop `quarantined`: {offending} row(s) in "
                f"{table_qualified}.{column} currently hold the value. "
                f"Migrate them to a real lifecycle state (e.g. `pending` "
                f"or `cancelled`) before re-running this migration."
            )

    # Step 2: rebuild the enum without `quarantined`.
    # For each column: DROP DEFAULT, ALTER TYPE with USING cast, SET DEFAULT
    # back to the captured expression cast to the new type. The default
    # text is re-evaluated against the new type, so a stored default of
    # `'pending'::taskstatus` becomes valid again because the new
    # `taskstatus` still has a `pending` member.
    op.execute("ALTER TYPE taskstatus RENAME TO taskstatus_old")
    members_sql = ", ".join(f"'{v}'" for v in _NEW_TASKSTATUS_MEMBERS)
    op.execute(f"CREATE TYPE taskstatus AS ENUM ({members_sql})")

    for table_qualified, column, default_expr in usages:
        if default_expr is not None:
            op.execute(
                f"ALTER TABLE {table_qualified} ALTER COLUMN {column} DROP DEFAULT"
            )
        op.execute(
            f"ALTER TABLE {table_qualified} "
            f"ALTER COLUMN {column} TYPE taskstatus "
            f"USING {column}::text::taskstatus"
        )
        if default_expr is not None:
            # Strip any explicit ::taskstatus_old cast in the captured
            # expression; the new column will re-cast against the
            # current `taskstatus`.
            cleaned = default_expr.replace("::taskstatus_old", "::taskstatus")
            op.execute(
                f"ALTER TABLE {table_qualified} "
                f"ALTER COLUMN {column} SET DEFAULT {cleaned}"
            )

    op.execute("DROP TYPE taskstatus_old")


def downgrade() -> None:
    """Re-add `quarantined` as a taskstatus member.

    Postgres allows adding a member to an existing enum directly, so the
    downgrade is a single ALTER. This restores the schema shape but does
    not (and cannot) re-introduce any row data — there was none.
    """
    op.execute("ALTER TYPE taskstatus ADD VALUE IF NOT EXISTS 'quarantined'")
