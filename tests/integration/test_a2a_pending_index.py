"""The pending-reply filter must be one ix_a2a_msg_pending can serve.

Migration 001 built the partial index ``WHERE requires_response = true``.
``get_inbox_summary`` filtered with ``.is_(True)`` (``IS TRUE``), which the
planner cannot match to that predicate, so on a migrated database its
pending counts never used the index. ``awaiting_response()`` is written
``= true``; sequential scans are disabled here so only the predicate decides
whether the index is usable, not the (empty) table's size.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from roboco.db.tables import A2AMessageTable
from roboco.services.a2a import awaiting_response
from sqlalchemy import func, select, text
from sqlalchemy.dialects import postgresql

if TYPE_CHECKING:
    from sqlalchemy import ColumnElement
    from sqlalchemy.ext.asyncio import AsyncSession


async def _plan(db_session: AsyncSession, pending: ColumnElement[bool]) -> str:
    statement = select(func.count()).select_from(A2AMessageTable).where(pending)
    sql = statement.compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    )
    await db_session.execute(text("SET LOCAL enable_seqscan = off"))
    rows = await db_session.execute(text(f"EXPLAIN {sql}"))
    return "\n".join(rows.scalars())


@pytest.mark.asyncio
async def test_awaiting_response_uses_the_pending_partial_index(
    db_session: AsyncSession,
) -> None:
    assert "ix_a2a_msg_pending" in await _plan(db_session, awaiting_response())


@pytest.mark.asyncio
async def test_an_is_true_filter_cannot_use_it(db_session: AsyncSession) -> None:
    """Why awaiting_response() is not written ``.is_(True)``."""
    is_true = A2AMessageTable.requires_response.is_(True)

    assert "ix_a2a_msg_pending" not in await _plan(db_session, is_true)
