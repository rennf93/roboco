"""Wave E2: agents.role column is typed as the postgres enum named 'agentrole'.

Anti-regression for the SQLAlchemy enum-naming binding. Without the
`_PG_ENUM_NAME_OVERRIDES["Role"] = "agentrole"` entry in
`roboco/db/tables.py`, SQLAlchemy infers the postgres type name from the
Python class name (`Role` → `role`), producing the `operator does not
exist: agentrole = role` regression smoke run 2 hit.

This test pins the invariant — every column typed `Role`/`AgentRole`
binds to the canonical `agentrole` postgres type.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_agents_role_uses_agentrole_type(db_session: AsyncSession) -> None:
    """agents.role.udt_name == 'agentrole' — never 'role'."""
    result = await db_session.execute(
        text(
            "SELECT udt_name FROM information_schema.columns "
            "WHERE table_name = 'agents' AND column_name = 'role'"
        )
    )
    row = result.first()
    assert row is not None, "agents.role column missing from test DB"
    assert row[0] == "agentrole", (
        f"agents.role typed as {row[0]!r}, expected 'agentrole' — "
        f"the SQLAlchemy enum binding is missing the name='agentrole' "
        f"override in roboco/db/tables.py::_PG_ENUM_NAME_OVERRIDES"
    )


@pytest.mark.asyncio
async def test_no_stray_role_enum_type_exists(db_session: AsyncSession) -> None:
    """No postgres enum named 'role' should exist — only 'agentrole'."""
    result = await db_session.execute(
        text(
            "SELECT typname FROM pg_type "
            "WHERE typname IN ('role', 'agentrole') "
            "ORDER BY typname"
        )
    )
    typenames = [row[0] for row in result]
    assert "agentrole" in typenames, "agentrole enum type missing"
    assert "role" not in typenames, (
        "stray 'role' enum type exists — Base.metadata.create_all "
        "produced both 'role' and 'agentrole'. Check "
        "roboco/db/tables.py::_str_enum for missing name= override."
    )
