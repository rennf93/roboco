"""roboco.services.gateway.evidence_repo coverage.

Most methods are still Phase 1 stubs returning empty lists;
``journal_highlights_for_task`` is wired to a real query, so it is tested
against a mocked ``execute`` result that stands in for the DB rows.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.evidence_repo import EvidenceRepo


@pytest.fixture
def repo() -> EvidenceRepo:
    """Build an EvidenceRepo with a stub session — no DB access in Phase 1."""
    return EvidenceRepo(MagicMock())


@pytest.mark.asyncio
async def test_constructor_stores_db_session() -> None:
    fake_db = MagicMock()
    repo = EvidenceRepo(fake_db)
    assert repo._db is fake_db


@pytest.mark.asyncio
async def test_list_unread_a2a_returns_empty(repo: EvidenceRepo) -> None:
    out = await repo.list_unread_a2a(uuid4())
    assert out == []


@pytest.mark.asyncio
async def test_list_unread_mentions_returns_empty(repo: EvidenceRepo) -> None:
    out = await repo.list_unread_mentions(uuid4())
    assert out == []


@pytest.mark.asyncio
async def test_list_pending_notifications_returns_empty(repo: EvidenceRepo) -> None:
    out = await repo.list_pending_notifications(uuid4())
    assert out == []


@pytest.mark.asyncio
async def test_task_metadata_gaps_returns_empty(repo: EvidenceRepo) -> None:
    out = await repo.task_metadata_gaps(uuid4())
    assert out == []


@pytest.mark.asyncio
async def test_recent_team_activity_returns_empty(repo: EvidenceRepo) -> None:
    out = await repo.recent_team_activity(uuid4())
    assert out == []


@pytest.mark.asyncio
async def test_blockers_in_lane_returns_empty(repo: EvidenceRepo) -> None:
    out = await repo.blockers_in_lane(uuid4())
    assert out == []


def _repo_with_rows(rows: list[object]) -> EvidenceRepo:
    """EvidenceRepo whose db.execute() yields a result with the given rows."""
    db = MagicMock()
    result = MagicMock()
    result.all.return_value = rows
    db.execute = AsyncMock(return_value=result)
    return EvidenceRepo(db)


@pytest.mark.asyncio
async def test_journal_highlights_for_task_empty_when_no_entries() -> None:
    repo = _repo_with_rows([])
    out = await repo.journal_highlights_for_task(uuid4())
    assert out == []


@pytest.mark.asyncio
async def test_journal_highlights_for_task_maps_rows_with_author() -> None:
    ts = datetime(2026, 6, 3, 5, 30, tzinfo=UTC)
    row = SimpleNamespace(
        type="decision_log",
        title="PO review",
        content="Approve with scope amendments.",
        timestamp=ts,
        slug="product-owner",
        role="product_owner",
    )
    repo = _repo_with_rows([row])
    out = await repo.journal_highlights_for_task(uuid4())
    assert out == [
        {
            "author": "product-owner",
            "author_role": "product_owner",
            "type": "decision_log",
            "title": "PO review",
            "content": "Approve with scope amendments.",
            "timestamp": ts.isoformat(),
        }
    ]
