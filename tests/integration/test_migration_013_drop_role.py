"""Wave A5 (2026-05-12): migration 013 drops the stray `role` postgres enum.

Smoke run 2 produced `UndefinedFunctionError: operator does not exist:
agentrole = role` because postgres had two enums for the same Python
class. The migration drops the unused one with a safety check.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_no_column_uses_role_type(db_session) -> None:  # type: ignore[no-untyped-def]
    """Before dropping, confirm no column actually uses the `role` type.

    If this ever fails it means a column was added that references the
    `role` enum and the migration must NOT be allowed to drop it — that
    column would become orphaned.
    """
    result = await db_session.execute(
        text(
            "SELECT column_name, table_name "
            "FROM information_schema.columns "
            "WHERE udt_name = 'role'"
        )
    )
    rows = list(result)
    assert rows == [], (
        f"columns still use `role` enum: {rows}; migration 013 cannot run"
    )


@pytest.mark.asyncio
async def test_role_enum_dropped_after_upgrade(db_session) -> None:  # type: ignore[no-untyped-def]
    """After migration 013 runs, only `agentrole` remains; `role` is gone."""
    # This test runs against a db where migrations have been applied to head.
    # The conftest fixture should handle that — verify by reading the
    # existing tests' conftest.
    result = await db_session.execute(
        text("SELECT typname FROM pg_type WHERE typname IN ('role', 'agentrole')")
    )
    rows = {row[0] for row in result}
    assert "agentrole" in rows, "agentrole must remain (it's the live enum)"
    assert "role" not in rows, "stray `role` enum should be dropped"
