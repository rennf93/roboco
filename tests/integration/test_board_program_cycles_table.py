"""Migration 087 tests — board_program_cycles table.

NOT a real alembic round-trip — the suite builds the test DB via
Base.metadata.create_all (see conftest); a real `alembic upgrade head` +
`downgrade -1` round trip against a scratch Postgres (:55432) was run
manually and confirmed clean (create + drop, no errors) as part of building
this migration. See `alembic/versions/087_board_program_cycles.py`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from roboco.db.tables import BoardProgramCycleTable

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_board_program_cycle_row_defaults(db_session: AsyncSession) -> None:
    """A freshly-inserted row gets zeroed counters and an empty decisions list."""
    row = BoardProgramCycleTable(program_key="roadmap")
    db_session.add(row)
    await db_session.flush()
    await db_session.refresh(row)

    assert row.items_proposed == 0
    assert row.items_approved == 0
    assert row.items_rejected == 0
    assert row.decisions == []
    assert row.opened_at is not None
    assert row.closed_at is None
    assert row.exploration_task_id is None


@pytest.mark.asyncio
async def test_board_program_cycle_round_trips_decisions(
    db_session: AsyncSession,
) -> None:
    """The decisions JSON column stores/returns a list of dicts byte-for-byte."""
    decisions = [
        {"item_ref": "item-1", "verdict": "approved", "reason": None},
        {"item_ref": "item-2", "verdict": "rejected", "reason": "not now"},
    ]
    row = BoardProgramCycleTable(
        program_key="roadmap",
        items_proposed=2,
        items_approved=1,
        items_rejected=1,
        decisions=decisions,
    )
    db_session.add(row)
    await db_session.flush()
    await db_session.refresh(row)

    assert row.decisions == decisions
