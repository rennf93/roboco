"""0.10.0 observability: tasks.revision_count + the audit_log query index.

Migration 045 adds ``tasks.revision_count`` (the O(1) rework counter —
forward-only, existing rows default to 0) and the composite index
``audit_log(target_id, event_type, timestamp)`` that powers the cycle-time and
rework reconstruction queries. The real upgrade/downgrade chain is verified
separately against a throwaway Postgres (see project migration-verification
discipline); these assertions guard the resulting schema shape.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


@pytest.mark.asyncio
async def test_revision_count_defaults_to_zero(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT column_default, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name = 'tasks' AND column_name = 'revision_count'"
        )
    )
    row = result.first()
    assert row is not None, "tasks.revision_count column must exist"
    assert row[1] == "NO", "revision_count must be NOT NULL"
    assert "0" in (row[0] or ""), "revision_count must default to 0"


@pytest.mark.asyncio
async def test_audit_log_query_index_exists(db_session: AsyncSession) -> None:
    result = await db_session.execute(
        text(
            "SELECT indexname FROM pg_indexes "
            "WHERE tablename = 'audit_log' "
            "AND indexname = 'ix_audit_log_target_event_ts'"
        )
    )
    assert result.first() is not None, (
        "composite index ix_audit_log_target_event_ts must exist on audit_log"
    )
