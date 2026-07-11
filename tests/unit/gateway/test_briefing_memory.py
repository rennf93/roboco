"""Org-memory keystone — role-shaped query + relevance-floored injection.

``shape_memory_query`` shapes the KB query per role; ``EvidenceRepo.similar_memory``
applies the cosine floor + top-K and returns the shaped items that
``_briefing_for`` injects as ``context_briefing["institutional_memory"]`` (only
when ``org_memory_enabled``).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.models.optimal import IndexType, SearchResult
from roboco.services.gateway.evidence_builder import shape_memory_query
from roboco.services.gateway.evidence_repo import EvidenceRepo

_FLOOR = 0.6
_HIGH = 0.9
_LOW = 0.4


def _result(score: float, index_type: IndexType = IndexType.LEARNINGS) -> SearchResult:
    return SearchResult(
        content="a distilled lesson body",
        source="roboco://learnings/lrn-1",
        score=score,
        index_type=index_type,
    )


def test_shape_memory_query_is_role_specific() -> None:
    dev = shape_memory_query("developer", "Add retry", "code")
    pm = shape_memory_query("cell_pm", "Add retry", "code")
    qa = shape_memory_query("qa", "Add retry", "code")
    doc = shape_memory_query("documenter", "Add retry", "documentation")
    assert dev != pm  # role shaping actually differs
    assert "Add retry" in dev and "implementation" in dev
    assert "decomposition" in pm
    assert "defect" in qa
    assert "documentation pattern" in doc


@pytest.mark.asyncio
async def test_similar_memory_applies_floor_and_shapes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    optimal = MagicMock()
    optimal.search = AsyncMock(
        return_value=[_result(_HIGH, IndexType.PLAYBOOKS), _result(_LOW)]
    )
    monkeypatch.setattr(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(return_value=optimal),
    )
    out = await EvidenceRepo(MagicMock()).similar_memory(
        query="q", top_k=3, min_score=_FLOOR
    )
    # the 0.4 result is below the floor, excluded; one met the floor → ok
    assert out["status"] == "ok"
    assert len(out["items"]) == 1
    assert out["items"][0]["kind"] == "playbook"
    assert out["items"][0]["score"] == _HIGH


@pytest.mark.asyncio
async def test_similar_memory_labels_vault_notes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    optimal = MagicMock()
    optimal.search = AsyncMock(return_value=[_result(_HIGH, IndexType.VAULT_NOTES)])
    monkeypatch.setattr(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(return_value=optimal),
    )
    out = await EvidenceRepo(MagicMock()).similar_memory(
        query="q", top_k=3, min_score=_FLOOR
    )
    assert out["status"] == "ok"
    assert out["items"][0]["kind"] == "vault_note"


@pytest.mark.asyncio
async def test_similar_memory_queries_vault_notes_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The claim-time briefing must reach the CEO's own vault notes, not just
    learnings/playbooks — the relevance floor is the only gate against bloat."""
    optimal = MagicMock()
    optimal.search = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(return_value=optimal),
    )
    await EvidenceRepo(MagicMock()).similar_memory(query="q", top_k=3, min_score=_FLOOR)
    _, kwargs = optimal.search.call_args
    assert IndexType.VAULT_NOTES in kwargs["context"].index_types


@pytest.mark.asyncio
async def test_similar_memory_caps_at_top_k(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    optimal = MagicMock()
    optimal.search = AsyncMock(return_value=[_result(_HIGH) for _ in range(5)])
    monkeypatch.setattr(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(return_value=optimal),
    )
    out = await EvidenceRepo(MagicMock()).similar_memory(
        query="q", top_k=2, min_score=_FLOOR
    )
    assert out["status"] == "ok"
    assert len(out["items"]) == 2  # noqa: PLR2004 - top_k cap


@pytest.mark.asyncio
async def test_similar_memory_error_status_on_rag_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(side_effect=RuntimeError("rag down")),
    )
    out = await EvidenceRepo(MagicMock()).similar_memory(
        query="q", top_k=3, min_score=_FLOOR
    )
    assert out == {"items": [], "status": "error"}


@pytest.mark.asyncio
async def test_similar_memory_empty_status_when_search_yields_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    optimal = MagicMock()
    optimal.search = AsyncMock(return_value=[])
    monkeypatch.setattr(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(return_value=optimal),
    )
    out = await EvidenceRepo(MagicMock()).similar_memory(
        query="q", top_k=3, min_score=_FLOOR
    )
    assert out == {"items": [], "status": "empty"}


@pytest.mark.asyncio
async def test_similar_memory_below_floor_status_when_all_under_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    optimal = MagicMock()
    optimal.search = AsyncMock(return_value=[_result(_LOW), _result(_LOW)])
    monkeypatch.setattr(
        "roboco.services.optimal.get_optimal_service",
        AsyncMock(return_value=optimal),
    )
    out = await EvidenceRepo(MagicMock()).similar_memory(
        query="q", top_k=3, min_score=_FLOOR
    )
    assert out == {"items": [], "status": "below_floor"}
