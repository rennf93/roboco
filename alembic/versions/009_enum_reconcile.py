"""Reconcile enum values with the ORM and add missing members.

Two adjustments to bring postgres enum types in line with the StrEnum
classes the ORM serializes:

1. Add missing values that were introduced after migration 001:
   - agentrole.system  (used internally for orchestrator-owned operations)
   - team.fullstack    (cross-team work)
   - taskstatus.quarantined (safe-park state for problematic tasks)

2. If the database was previously bootstrapped via Base.metadata.create_all
   (which uses the StrEnum member NAME — uppercase), reconcile the enum
   values to lowercase so they match alembic 001's declared values and
   the new ORM (Enum(..., values_callable=...)) serialization. This is a
   conditional rebuild: if the enum already has lowercase members the
   block is a no-op.

Revision ID: 009_enum_reconcile
Revises: 008_align_skills
Create Date: 2026-05-02
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from alembic import op
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.sql.elements import TextClause

revision = "009_enum_reconcile"
down_revision = "008_align_skills"
branch_labels = None
depends_on = None


# Per-enum desired member set (matches the StrEnum `.value` lists in
# roboco/models/base.py + roboco/models/a2a.py + work_session.py).
_DESIRED: dict[str, tuple[str, ...]] = {
    "agentrole": (
        "system",
        "ceo",
        "product_owner",
        "head_marketing",
        "auditor",
        "main_pm",
        "cell_pm",
        "developer",
        "qa",
        "documenter",
    ),
    "team": (
        "backend",
        "frontend",
        "ux_ui",
        "fullstack",
        "main_pm",
        "board",
        "marketing",
    ),
    "taskstatus": (
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
        "quarantined",
    ),
}


# (enum_name, table_name, column_name) — used by the conditional rebuild.
_USAGES: tuple[tuple[str, str, str], ...] = (
    ("agentrole", "agents", "role"),
    ("team", "agents", "team"),
    ("team", "tasks", "team"),
    ("team", "projects", "assigned_cell"),
    ("taskstatus", "tasks", "status"),
)


def upgrade() -> None:
    """Add missing values; rebuild if uppercase drift is detected."""
    bind = op.get_bind()

    # Step 1: detect drift. If any enum has uppercase members, the DB was
    # bootstrapped via create_all and needs a full rebuild for ALL the
    # enums we use. If everything is lowercase already, just ADD missing
    # values one by one (the cheap path).
    drifted = bool(
        bind.execute(
            _drift_query(),
        ).scalar()
    )

    if not drifted:
        # Cheap path: ADD missing values per enum.
        for enum_name, members in _DESIRED.items():
            for value in members:
                op.execute(f"ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS '{value}'")
        return

    # Drift path: rebuild the affected enums. Each rebuild is a
    # rename-old / create-new / alter-column / drop-old sequence. We
    # USING lower(col::text)::new_enum to convert uppercase data.
    for enum_name, members in _DESIRED.items():
        op.execute(f"ALTER TYPE {enum_name} RENAME TO {enum_name}_old")
        members_sql = ", ".join(f"'{v}'" for v in members)
        op.execute(f"CREATE TYPE {enum_name} AS ENUM ({members_sql})")

    for enum_name, table, column in _USAGES:
        op.execute(
            f"ALTER TABLE {table} "
            f"ALTER COLUMN {column} TYPE {enum_name} "
            f"USING lower({column}::text)::{enum_name}"
        )

    for enum_name in _DESIRED:
        op.execute(f"DROP TYPE {enum_name}_old")


def downgrade() -> None:
    """Remove the values added by upgrade.

    Postgres has no DROP VALUE primitive; the only way to remove an enum
    member is the rebuild dance from upgrade. For the no-drift path we
    simply leave the added values in place — they cause no harm and
    backing them out would be a destructive rebuild on healthy data. For
    the drift path we cannot recover the prior uppercase values without
    losing referential integrity, so we likewise leave the rebuild in
    place. This downgrade is therefore intentionally a no-op.
    """
    return None


def _drift_query() -> TextClause:
    """SELECT true iff any tracked enum has uppercase members."""
    enum_list = ", ".join(f"'{name}'" for name in _DESIRED)
    return text(
        f"""
        SELECT EXISTS (
            SELECT 1
            FROM pg_enum e
            JOIN pg_type t ON e.enumtypid = t.oid
            WHERE t.typname IN ({enum_list})
              AND e.enumlabel ~ '[A-Z]'
        )
        """
    )
