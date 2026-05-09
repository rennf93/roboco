"""Reconcile every postgres enum with the ORM (lowercase) + add new members.

Two adjustments to bring postgres enum types in line with the StrEnum
classes the ORM serializes:

1. Add missing members that were introduced after migration 001:
   - agentrole.system        (orchestrator-owned operations)
   - team.fullstack          (cross-team work)
   - taskstatus.quarantined  (safe-park state for problematic tasks)

2. If any enum was previously bootstrapped via Base.metadata.create_all
   (which uses the StrEnum member NAME — uppercase), reconcile every
   affected enum to lowercase so values match alembic 001's declared
   members and the new ORM serialization (Enum(..., values_callable=...)).

The reconcile path is dynamic: any enum found with uppercase members is
rebuilt by renaming the old type, creating a fresh lowercase type with
the same members (lower-cased) plus any desired additions, ALTER-ing
every column that references the type with `USING lower(col::text)::T`,
then dropping the old type. Repeats per affected enum.

Revision ID: 009_enum_reconcile
Revises: 008_align_skills
Create Date: 2026-05-02
"""

from __future__ import annotations

from alembic import context, op
from sqlalchemy import text

revision = "009_enum_reconcile"
down_revision = "008_align_skills"
branch_labels = None
depends_on = None


# Members the ORM defines that may not be in the alembic-declared enum.
# Every value here is a literal already used by the ORM today; adding
# them to the postgres enum is what unblocks the next-write path.
_DESIRED_ADDITIONS: dict[str, tuple[str, ...]] = {
    "agentrole": ("system",),
    "team": ("fullstack",),
    "taskstatus": ("quarantined",),
}


def upgrade() -> None:
    """Add missing values; rebuild any enum found with uppercase members.

    This migration is fundamentally introspective — it queries pg_enum at
    runtime to find drifted types and rebuild them. There is no
    representable equivalent in offline (--sql) mode, so we emit the
    additive ALTER TYPE statements only and skip the rebuild branch.
    Run against a real DB to perform the reconcile.
    """
    if context.is_offline_mode():
        for enum_name, additions in _DESIRED_ADDITIONS.items():
            for value in additions:
                op.execute(
                    f"ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS '{value}'"
                )
        return
    bind = op.get_bind()

    # Step 1: find every enum that has at least one uppercase member.
    drifted = [
        row[0]
        for row in bind.execute(
            text(
                """
                SELECT DISTINCT t.typname
                FROM pg_enum e
                JOIN pg_type t ON e.enumtypid = t.oid
                WHERE e.enumlabel ~ '[A-Z]'
                ORDER BY t.typname
                """
            )
        )
    ]

    if not drifted:
        # Cheap path: add the missing values for known additions.
        for enum_name, additions in _DESIRED_ADDITIONS.items():
            for value in additions:
                op.execute(f"ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS '{value}'")
        return

    # Step 2: rebuild each drifted enum.
    # For each enum:
    #   - read current members
    #   - construct new member list as lowercase(current) plus desired_additions
    #   - rename old, create new, ALTER every (table, column) using it,
    #     drop old.
    for enum_name in drifted:
        current = [
            row[0]
            for row in bind.execute(
                text(
                    """
                    SELECT e.enumlabel
                    FROM pg_enum e
                    JOIN pg_type t ON e.enumtypid = t.oid
                    WHERE t.typname = :name
                    ORDER BY e.enumsortorder
                    """
                ),
                {"name": enum_name},
            )
        ]
        new_members: list[str] = []
        seen: set[str] = set()
        for member in current:
            lowered = member.lower()
            if lowered not in seen:
                new_members.append(lowered)
                seen.add(lowered)
        for addition in _DESIRED_ADDITIONS.get(enum_name, ()):
            if addition not in seen:
                new_members.append(addition)
                seen.add(addition)

        usages = [
            (row[0], row[1])
            for row in bind.execute(
                text(
                    """
                    SELECT n.nspname || '.' || c.relname AS table_qualified, a.attname
                    FROM pg_attribute a
                    JOIN pg_class c ON a.attrelid = c.oid
                    JOIN pg_namespace n ON c.relnamespace = n.oid
                    JOIN pg_type t ON a.atttypid = t.oid
                    WHERE t.typname = :name
                      AND a.attnum > 0
                      AND NOT a.attisdropped
                      AND c.relkind = 'r'
                    """
                ),
                {"name": enum_name},
            )
        ]

        op.execute(f"ALTER TYPE {enum_name} RENAME TO {enum_name}_old")
        members_sql = ", ".join(f"'{v}'" for v in new_members)
        op.execute(f"CREATE TYPE {enum_name} AS ENUM ({members_sql})")

        for table_qualified, column in usages:
            op.execute(
                f"ALTER TABLE {table_qualified} "
                f"ALTER COLUMN {column} TYPE {enum_name} "
                f"USING lower({column}::text)::{enum_name}"
            )

        op.execute(f"DROP TYPE {enum_name}_old")


def downgrade() -> None:
    """Intentional no-op.

    Postgres has no DROP VALUE primitive — removing an enum member would
    require the same rebuild dance from upgrade. We can't recover the
    prior uppercase shape after the data was already lowercased without
    losing referential integrity, so we leave the reconciled state in
    place.
    """
    return None
