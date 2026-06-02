"""Backfill ORM enum values the migration chain never added.

Revision ID: 020_backfill_enum_values
Revises: 019_seed_default_providers
Create Date: 2026-06-02

Several StrEnum values were added to the ORM (roboco/models/base.py) over time
WITHOUT a matching `ALTER TYPE ... ADD VALUE` migration. Migration 017 was
autogenerate-derived, and Alembic autogenerate does NOT detect added enum
labels — so the drift survived. On any database whose `<enum>` type predates the
ORM addition (a from-base migrate that lacks the value, or a create_all-origin
DB stamped forward), binding the value raises at runtime, e.g.:

    invalid input value for enum notificationtype: "a2a_request"
    (GET /api/notifications -> list_system_notifications)

This adds every drifted value idempotently. `ADD VALUE IF NOT EXISTS` is a
no-op when the label already exists (e.g. 009_enum_reconcile already lowercased
it), so this is safe on every database state. PG 16 permits ADD VALUE inside a
transaction as long as the new value is not USED in the same transaction (we
only add), matching how 002/009/011/012 already add enum values.

Drift detected by comparing each ORM enum's values to the labels the migration
chain produces (CREATE TYPE + ALTER TYPE ADD VALUE).
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "020_backfill_enum_values"
down_revision = "019_seed_default_providers"
branch_labels = None
depends_on = None


# (postgres enum type name) -> ORM values that may be missing. Values are the
# lowercase StrEnum `.value` the ORM binds/reads via `_str_enum`. This set is the
# union of (a) ORM values the migration chain never produces and (b) values a
# live create_all-origin DB (stamped past the ALTER-TYPE-ADD-VALUE migrations)
# is missing — confirmed by a direct read of the running DB's pg_enum. e.g.
# `team.fullstack` is added by an early migration the live DB never ran, so the
# static chain comparison missed it; only the live read surfaced it.
_MISSING: dict[str, tuple[str, ...]] = {
    "notificationtype": ("a2a_request", "approval", "knowledge_share", "mention"),
    "blockerresolvertype": ("agent", "human"),
    "handoffstatus": ("accepted",),
    "team": ("fullstack", "system"),
}


def upgrade() -> None:
    for enum_name, values in _MISSING.items():
        for value in values:
            op.execute(f"ALTER TYPE {enum_name} ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # Postgres has no `ALTER TYPE ... DROP VALUE`. Removing an enum label
    # requires the full rename-recreate-recast dance (see 009/011) and is
    # unsafe to reverse blindly, so this is intentionally a no-op: the added
    # labels are harmless if unused.
    pass
