"""memory_retrieval_log is pruned by the trajectory labeler pass.

Retrieval events only grade decision rows made before them, and decision
rows are pruned past ``decisions_log_retention_days``; events older than
that window can grade nothing, so the pass deletes them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from roboco.config import settings
from roboco.db.tables import MemoryRetrievalLogTable
from roboco.services.decisions.trajectory import (
    prune_memory_retrieval_log,
    run_trajectory_pass,
)
from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


async def _seed(db_session: AsyncSession) -> None:
    now = datetime.now(UTC)
    window = timedelta(days=settings.decisions_log_retention_days)
    for source, age in (
        ("roboco://learnings/expired", window + timedelta(hours=1)),
        ("roboco://learnings/kept", window - timedelta(hours=1)),
    ):
        db_session.add(
            MemoryRetrievalLogTable(
                created_at=now - age, source=source, retrieved_at=now - age
            )
        )
    await db_session.flush()


async def _sources(db_session: AsyncSession) -> list[str]:
    rows = await db_session.execute(select(MemoryRetrievalLogTable.source))
    return list(rows.scalars())


@pytest.mark.asyncio
async def test_prune_deletes_only_events_past_the_decision_log_window(
    db_session: AsyncSession,
) -> None:
    await _seed(db_session)

    assert await prune_memory_retrieval_log(db_session) == 1
    assert await _sources(db_session) == ["roboco://learnings/kept"]


@pytest.mark.asyncio
async def test_the_trajectory_pass_runs_the_prune(db_session: AsyncSession) -> None:
    await _seed(db_session)

    await run_trajectory_pass(db_session)

    assert await _sources(db_session) == ["roboco://learnings/kept"]
