"""Wave B2 (2026-05-12, re-scoped): migration 014 drops pm_approvals.

Original spec proposed dropping three Task columns; investigation found
quick_context and proactive_context are actively used (original_developer
tracking, RAG context). Only pm_approvals is truly orphaned.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_pm_approvals_dropped(db_session) -> None:  # type: ignore[no-untyped-def]
    """pm_approvals column is gone from the tasks table."""
    result = await db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'tasks' AND column_name = 'pm_approvals'"
        )
    )
    rows = list(result)
    assert rows == [], "pm_approvals column should have been dropped"


@pytest.mark.asyncio
async def test_quick_context_and_proactive_context_remain(db_session) -> None:  # type: ignore[no-untyped-def]
    """quick_context and proactive_context MUST remain — they're actively used."""
    result = await db_session.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'tasks' "
            "AND column_name IN ('quick_context', 'proactive_context')"
        )
    )
    rows = {r[0] for r in result}
    assert "quick_context" in rows, (
        "quick_context must remain (original_developer + audit)"
    )
    assert "proactive_context" in rows, "proactive_context must remain (RAG injection)"
