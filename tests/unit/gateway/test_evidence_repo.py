"""roboco.services.gateway.evidence_repo coverage — Phase 1 stubs.

EvidenceRepo currently returns empty lists for every method (Phase 1).
Tests confirm the contract and the constructor stores the session — when
Phase 2+ wires real queries, these tests will need real-DB integration.
"""

from __future__ import annotations

from unittest.mock import MagicMock
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


@pytest.mark.asyncio
async def test_journal_highlights_for_task_returns_empty(repo: EvidenceRepo) -> None:
    out = await repo.journal_highlights_for_task(uuid4())
    assert out == []
