"""roboco.services.gateway.evidence_repo coverage (mapping + empty paths).

These are mocked unit tests for the row-mapping and empty/not-found paths. The
actual SQL (WHERE clauses, array operators) is exercised against a real DB in
tests/integration/test_evidence_repo_queries.py.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from roboco.services.gateway.evidence_repo import (
    _ANCESTOR_DESC_CAP,
    _HIERARCHY_DEPTH_CAP,
    EvidenceRepo,
)


def _scalar_result(value: object) -> MagicMock:
    """A query result whose ``scalar_one_or_none()`` returns ``value``."""
    r = MagicMock()
    r.scalar_one_or_none.return_value = value
    return r


def _rows_result(rows: list[object]) -> MagicMock:
    """A query result whose ``all()`` returns ``rows``."""
    r = MagicMock()
    r.all.return_value = rows
    return r


def _empty_repo() -> EvidenceRepo:
    """Repo whose scalar() and execute() both yield nothing."""
    db = MagicMock()
    db.scalar = AsyncMock(return_value=None)
    result = MagicMock()
    result.all.return_value = []
    result.scalars.return_value.all.return_value = []
    db.execute = AsyncMock(return_value=result)
    return EvidenceRepo(db)


def _repo_with_rows(rows: list[object], *, scalar: object = None) -> EvidenceRepo:
    """Repo whose execute().all()/scalars().all() yields rows; scalar() → scalar."""
    db = MagicMock()
    db.scalar = AsyncMock(return_value=scalar)
    result = MagicMock()
    result.all.return_value = rows
    result.scalars.return_value.all.return_value = rows
    db.execute = AsyncMock(return_value=result)
    return EvidenceRepo(db)


def _repo_with_goals_row(row: object | None) -> EvidenceRepo:
    """Repo whose execute().scalar_one_or_none() yields the singleton row."""
    db = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    db.execute = AsyncMock(return_value=result)
    return EvidenceRepo(db)


@pytest.mark.asyncio
async def test_company_goals_none_when_no_row() -> None:
    assert await _repo_with_goals_row(None).company_goals() is None


@pytest.mark.asyncio
async def test_company_goals_none_when_empty_charter() -> None:
    row = SimpleNamespace(
        north_star="",
        objectives=[],
        constraints=[],
        operating_policy={},
        brand_voice="",
    )
    assert await _repo_with_goals_row(row).company_goals() is None


@pytest.mark.asyncio
async def test_company_goals_compact_dict_when_set() -> None:
    row = SimpleNamespace(
        north_star="Win the market",
        objectives=[{"metric": "NPS", "target": 50}],
        constraints=["AGPL"],
        operating_policy={"autonomy_level": "assisted"},
        brand_voice="Confident, dry wit.",
    )
    goals = await _repo_with_goals_row(row).company_goals()
    assert goals == {
        "north_star": "Win the market",
        "objectives": [{"metric": "NPS", "target": 50}],
        "constraints": ["AGPL"],
        "operating_policy": {"autonomy_level": "assisted"},
        "brand_voice": "Confident, dry wit.",
    }


@pytest.mark.asyncio
async def test_company_goals_none_when_only_brand_voice_unset() -> None:
    """A charter with substantive content but no brand_voice must still
    surface — brand_voice is additive, not required."""
    row = SimpleNamespace(
        north_star="Win the market",
        objectives=[],
        constraints=[],
        operating_policy={},
        brand_voice="",
    )
    goals = await _repo_with_goals_row(row).company_goals()
    assert goals is not None
    assert goals["brand_voice"] == ""


@pytest.mark.asyncio
async def test_company_goals_surfaces_when_only_brand_voice_set() -> None:
    """The inverse: brand_voice alone (everything else empty) must still
    surface — it counts toward the "charter has content" check."""
    row = SimpleNamespace(
        north_star="",
        objectives=[],
        constraints=[],
        operating_policy={},
        brand_voice="Speak as 'we'.",
    )
    goals = await _repo_with_goals_row(row).company_goals()
    assert goals is not None
    assert goals["brand_voice"] == "Speak as 'we'."


@pytest.mark.asyncio
async def test_constructor_stores_db_session() -> None:
    fake_db = MagicMock()
    assert EvidenceRepo(fake_db)._db is fake_db


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method",
    [
        "list_unread_a2a",
        "list_unread_mentions",
        "list_pending_notifications",
        "task_metadata_gaps",
        "recent_team_activity",
        "blockers_in_lane",
    ],
)
async def test_methods_return_empty_when_no_data(method: str) -> None:
    repo = _empty_repo()
    assert await getattr(repo, method)(uuid4()) == []


@pytest.mark.asyncio
async def test_pending_notifications_maps_rows() -> None:
    ts = datetime(2026, 6, 9, 19, 27, tzinfo=UTC)
    nid, frm, tid = uuid4(), uuid4(), uuid4()
    row = SimpleNamespace(
        id=nid,
        type="alert",
        priority="high",
        subject="CEO change request",
        body="redo the contract",
        from_agent=frm,
        related_task_id=tid,
        timestamp=ts,
    )
    out = await _repo_with_rows([row]).list_pending_notifications(uuid4())
    assert out == [
        {
            "notification_id": str(nid),
            "type": "alert",
            "priority": "high",
            "subject": "CEO change request",
            "body": "redo the contract",
            "from_agent": str(frm),
            "task_id": str(tid),
            "timestamp": ts.isoformat(),
        }
    ]


@pytest.mark.asyncio
async def test_unread_a2a_maps_other_agent_and_unread_count() -> None:
    cid = uuid4()
    conv = SimpleNamespace(
        id=cid,
        agent_a="be-pm",
        agent_b="main-pm",
        unread_by_a=3,
        unread_by_b=0,
        topic="rework",
        task_id=None,
    )
    # list_unread_a2a now selects (conversation, last_incoming_preview) tuples.
    out = await _repo_with_rows(
        [(conv, "please redo the auth check")], scalar="be-pm"
    ).list_unread_a2a(uuid4())
    assert out == [
        {
            "conversation_id": str(cid),
            "from_agent": "main-pm",
            "unread": 3,
            "topic": "rework",
            "task_id": None,
            "last_message_preview": "please redo the auth check",
        }
    ]


@pytest.mark.asyncio
async def test_task_metadata_gaps_flags_missing_fields() -> None:
    repo = _repo_with_rows(
        [], scalar=SimpleNamespace(acceptance_criteria=[], description="")
    )
    assert await repo.task_metadata_gaps(uuid4()) == [
        "no acceptance criteria",
        "no description",
    ]


@pytest.mark.asyncio
async def test_journal_highlights_for_task_empty_when_no_entries() -> None:
    assert await _repo_with_rows([]).journal_highlights_for_task(uuid4()) == []


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
    out = await _repo_with_rows([row]).journal_highlights_for_task(uuid4())
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


# ---------------------------------------------------------------------------
# Parent-chain walk + ancestor context (the intake-analysis torch carrier).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ancestor_task_ids_walks_parent_to_root() -> None:
    leaf, parent, grand = uuid4(), uuid4(), uuid4()
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _scalar_result(parent),
            _scalar_result(grand),
            _scalar_result(None),  # grand is a root
        ]
    )
    repo = EvidenceRepo(db)
    assert await repo._ancestor_task_ids(leaf) == [parent, grand]


@pytest.mark.asyncio
async def test_ancestor_task_ids_cycle_guard_stops() -> None:
    leaf, parent = uuid4(), uuid4()
    db = MagicMock()
    # leaf -> parent -> leaf (cycles back to the start, already in `seen`).
    db.execute = AsyncMock(side_effect=[_scalar_result(parent), _scalar_result(leaf)])
    repo = EvidenceRepo(db)
    assert await repo._ancestor_task_ids(leaf) == [parent]


@pytest.mark.asyncio
async def test_ancestor_task_ids_missing_row_returns_empty() -> None:
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_scalar_result(None)])
    repo = EvidenceRepo(db)
    assert await repo._ancestor_task_ids(uuid4()) == []


@pytest.mark.asyncio
async def test_ancestor_task_ids_depth_capped() -> None:
    start = uuid4()
    chain = [uuid4() for _ in range(_HIERARCHY_DEPTH_CAP + 5)]
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_scalar_result(p) for p in chain])
    repo = EvidenceRepo(db)
    result = await repo._ancestor_task_ids(start)
    assert len(result) == _HIERARCHY_DEPTH_CAP
    assert result == chain[:_HIERARCHY_DEPTH_CAP]


@pytest.mark.asyncio
async def test_ancestor_context_for_task_parentless_returns_empty() -> None:
    db = MagicMock()
    db.execute = AsyncMock(side_effect=[_scalar_result(None)])
    repo = EvidenceRepo(db)
    assert await repo.ancestor_context_for_task(uuid4()) == []


@pytest.mark.asyncio
async def test_ancestor_context_for_task_maps_chain_with_depth() -> None:
    leaf, parent, grand = uuid4(), uuid4(), uuid4()
    row_p = SimpleNamespace(id=parent, title="Cell PM slice", description="p-desc")
    row_g = SimpleNamespace(id=grand, title="Root", description="g-desc")
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _scalar_result(parent),
            _scalar_result(grand),
            _scalar_result(None),  # end of _ancestor_task_ids
            _rows_result([row_p, row_g]),  # the batch fetch
        ]
    )
    repo = EvidenceRepo(db)
    assert await repo.ancestor_context_for_task(leaf) == [
        {
            "task_id": str(parent),
            "depth": 1,
            "title": "Cell PM slice",
            "description": "p-desc",
        },
        {"task_id": str(grand), "depth": 2, "title": "Root", "description": "g-desc"},
    ]


@pytest.mark.asyncio
async def test_ancestor_context_for_task_skips_missing_ancestor_row() -> None:
    leaf, parent, grand = uuid4(), uuid4(), uuid4()
    row_p = SimpleNamespace(id=parent, title="Cell PM slice", description="p-desc")
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _scalar_result(parent),
            _scalar_result(grand),
            _scalar_result(None),
            _rows_result([row_p]),  # grand's row is missing from the batch
        ]
    )
    repo = EvidenceRepo(db)
    assert await repo.ancestor_context_for_task(leaf) == [
        {
            "task_id": str(parent),
            "depth": 1,
            "title": "Cell PM slice",
            "description": "p-desc",
        },
    ]


@pytest.mark.asyncio
async def test_ancestor_context_for_task_clips_long_description() -> None:
    leaf, parent = uuid4(), uuid4()
    row_p = SimpleNamespace(
        id=parent, title="P", description="x" * (_ANCESTOR_DESC_CAP + 500)
    )
    db = MagicMock()
    db.execute = AsyncMock(
        side_effect=[
            _scalar_result(parent),
            _scalar_result(None),
            _rows_result([row_p]),
        ]
    )
    repo = EvidenceRepo(db)
    ctx = await repo.ancestor_context_for_task(leaf)
    assert len(ctx[0]["description"]) == _ANCESTOR_DESC_CAP
