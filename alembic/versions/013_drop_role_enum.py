"""Drop stray `role` postgres enum.

Smoke run 2 (2026-05-11) surfaced `UndefinedFunctionError: operator
does not exist: agentrole = role` because postgres ended up with two
enums (`role` and `agentrole`) for the same Python `Role` class. Only
agents.role column uses `agentrole`; nothing uses `role`. This migration
drops the orphan.

The cause (E2 in the spec) is a SQLAlchemy parameter-binding edge case
where a query parameter wasn't bound with the column's enum name,
letting SQLAlchemy infer a new type from the Python class name.
Investigation tracked in E2; this migration handles the symptom.

Revision ID: 013_drop_role_enum
Revises: 012_align_agentrole_foundation
Create Date: 2026-05-12
"""

from __future__ import annotations

from alembic import context, op
from sqlalchemy import text

revision = "013_drop_role_enum"
down_revision = "012_align_agentrole_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Drop the orphan `role` enum after confirming no column uses it."""
    if context.is_offline_mode():
        # Offline mode: emit the SQL but skip the safety check.
        op.execute("DROP TYPE IF EXISTS role")
        return

    conn = op.get_bind()
    # Safety check: no column may still reference the type
    result = conn.execute(
        text(
            "SELECT column_name, table_name "
            "FROM information_schema.columns "
            "WHERE udt_name = 'role'"
        )
    )
    rows = list(result)
    if rows:
        raise RuntimeError(
            f"refusing to DROP TYPE role: columns still reference it: {rows}"
        )

    conn.execute(text("DROP TYPE IF EXISTS role"))


def downgrade() -> None:
    """Recreate the enum with the foundation's Role values.

    Used if we ever need to revert this migration. No rows reference
    the type after the upgrade ran, so recreation is a no-op for app
    state.
    """
    op.execute(
        "CREATE TYPE role AS ENUM ("
        "'system', 'developer', 'qa', 'documenter', 'cell_pm', 'main_pm', "
        "'product_owner', 'head_marketing', 'auditor', 'ceo'"
        ")"
    )
