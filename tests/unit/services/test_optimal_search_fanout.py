"""OptimalService.search embeds the query once and fans out across indexes.

Guards the latency fix: previously each index re-ran HyDE + embed sequentially
(N LLM calls, serial), so an all-index search took ~28s. Now the query is
embedded once and every index's vector search runs concurrently with that single
embedding.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.models.optimal import IndexType, SearchOutcome, SearchResult
from roboco.services.optimal import OptimalService


def _fake_plugin(index_type: IndexType) -> MagicMock:
    plugin = MagicMock()
    plugin.compute_query_embedding = AsyncMock(return_value=[0.1, 0.2, 0.3])
    plugin.search_with_embedding = AsyncMock(
        return_value=SearchOutcome(results=[], success=True, index_type=index_type)
    )
    plugin.count = AsyncMock(return_value=1)
    return plugin


@pytest.mark.asyncio
async def test_search_embeds_once_and_fans_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    svc = OptimalService.__new__(OptimalService)
    p_docs = _fake_plugin(IndexType.DOCUMENTATION)
    p_journals = _fake_plugin(IndexType.JOURNALS)
    monkeypatch.setattr(svc, "_initialized", True, raising=False)
    monkeypatch.setattr(
        svc,
        "_plugins",
        {IndexType.DOCUMENTATION: p_docs, IndexType.JOURNALS: p_journals},
        raising=False,
    )

    await svc.search("anything")

    # Embedded exactly once total (on the first plugin), reused across indexes —
    # not once per index.
    embed_calls = (
        p_docs.compute_query_embedding.await_count
        + p_journals.compute_query_embedding.await_count
    )
    assert embed_calls == 1
    # Every index ran a vector search with the pre-computed embedding.
    p_docs.search_with_embedding.assert_awaited_once()
    p_journals.search_with_embedding.assert_awaited_once()


@pytest.mark.asyncio
async def test_search_with_gaps_returns_empty_gaps_when_all_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """search_with_gaps returns (results, gaps) — gaps empty when all finish."""
    svc = OptimalService.__new__(OptimalService)
    p_docs = _fake_plugin(IndexType.DOCUMENTATION)
    p_journals = _fake_plugin(IndexType.JOURNALS)
    monkeypatch.setattr(svc, "_initialized", True, raising=False)
    monkeypatch.setattr(
        svc,
        "_plugins",
        {IndexType.DOCUMENTATION: p_docs, IndexType.JOURNALS: p_journals},
        raising=False,
    )

    results, gaps = await svc.search_with_gaps("anything")

    assert isinstance(results, list)
    assert gaps == []


@pytest.mark.asyncio
async def test_search_with_gaps_records_timeout_in_gaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On index timeout, search_with_gaps returns partial results and names the gap."""
    svc = OptimalService.__new__(OptimalService)
    p_docs = _fake_plugin(IndexType.DOCUMENTATION)
    p_journals = _fake_plugin(IndexType.JOURNALS)
    p_journals.search_with_embedding = AsyncMock(side_effect=TimeoutError())
    monkeypatch.setattr(svc, "_initialized", True, raising=False)
    monkeypatch.setattr(
        svc,
        "_plugins",
        {IndexType.DOCUMENTATION: p_docs, IndexType.JOURNALS: p_journals},
        raising=False,
    )

    results, gaps = await svc.search_with_gaps("anything")

    assert isinstance(results, list)
    assert len(gaps) == 1
    assert "journals" in gaps[0]
    assert "timed out" in gaps[0]
    # Docs index still ran its search — partial results, not a 504 discard.
    p_docs.search_with_embedding.assert_awaited_once()


@pytest.mark.asyncio
async def test_search_with_gaps_records_non_timeout_failure_in_gaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a non-timeout index failure, gaps names the index with 'failed'.

    Previously only TimeoutError was caught, so a failed SearchOutcome was
    logged and silently dropped from the gaps list.
    """
    svc = OptimalService.__new__(OptimalService)
    p_docs = _fake_plugin(IndexType.DOCUMENTATION)
    p_journals = _fake_plugin(IndexType.JOURNALS)
    p_journals.search_with_embedding = AsyncMock(
        side_effect=RuntimeError("vector store unreachable")
    )
    monkeypatch.setattr(svc, "_initialized", True, raising=False)
    monkeypatch.setattr(
        svc,
        "_plugins",
        {IndexType.DOCUMENTATION: p_docs, IndexType.JOURNALS: p_journals},
        raising=False,
    )

    results, gaps = await svc.search_with_gaps("anything")

    assert results == []
    assert len(gaps) == 1
    assert "journals" in gaps[0]
    assert "failed: RuntimeError" in gaps[0]
    # Distinct from a timeout entry: no "timed out" wording.
    assert "timed out" not in gaps[0]
    # Docs index still ran its search — partial fan-out, not a discard.
    p_docs.search_with_embedding.assert_awaited_once()


@pytest.mark.asyncio
async def test_search_records_plugin_failure_outcome_in_gaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A plugin that RETURNS SearchOutcome(success=False) is a gap too.

    Some base plugins signal failure via the outcome instead of raising; that
    path was previously logged in _collect_outcomes but never named in gaps.
    """
    svc = OptimalService.__new__(OptimalService)
    p_docs = _fake_plugin(IndexType.DOCUMENTATION)
    p_docs.search_with_embedding = AsyncMock(
        return_value=SearchOutcome(
            results=[],
            success=False,
            error_message="vector store unreachable",
            index_type=IndexType.DOCUMENTATION,
        )
    )
    p_journals = _fake_plugin(IndexType.JOURNALS)
    monkeypatch.setattr(svc, "_initialized", True, raising=False)
    monkeypatch.setattr(
        svc,
        "_plugins",
        {IndexType.DOCUMENTATION: p_docs, IndexType.JOURNALS: p_journals},
        raising=False,
    )

    results, gaps = await svc.search_with_gaps("anything")

    assert results == []
    assert gaps == ["documentation unavailable: failed: vector store unreachable"]


@pytest.mark.asyncio
async def test_query_records_plugin_failure_outcome_in_gaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rag path records a returned failure outcome in gaps, not just errors."""
    svc = OptimalService.__new__(OptimalService)
    p_docs = _fake_plugin(IndexType.DOCUMENTATION)
    p_journals = _fake_plugin(IndexType.JOURNALS)
    p_journals.search_with_embedding = AsyncMock(
        return_value=SearchOutcome(
            results=[],
            success=False,
            error_message="backend unavailable",
            index_type=IndexType.JOURNALS,
        )
    )
    monkeypatch.setattr(svc, "_initialized", True, raising=False)
    monkeypatch.setattr(
        svc,
        "_plugins",
        {IndexType.DOCUMENTATION: p_docs, IndexType.JOURNALS: p_journals},
        raising=False,
    )

    response = await svc.query("anything")

    assert "journals unavailable: failed: backend unavailable" in response.gaps
    # The error is still recorded in search_errors for the rag response.
    assert response.search_errors["journals"] == "backend unavailable"


@pytest.mark.asyncio
async def test_search_partial_non_timeout_failure_keeps_other_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing index doesn't discard results from the indexes that succeeded."""
    svc = OptimalService.__new__(OptimalService)
    p_docs = _fake_plugin(IndexType.DOCUMENTATION)
    p_docs.search_with_embedding = AsyncMock(
        return_value=SearchOutcome(
            results=[
                SearchResult(
                    content="doc hit",
                    source="docs/x.md",
                    score=0.9,
                    index_type=IndexType.DOCUMENTATION,
                )
            ],
            success=True,
            index_type=IndexType.DOCUMENTATION,
        )
    )
    p_journals = _fake_plugin(IndexType.JOURNALS)
    p_journals.search_with_embedding = AsyncMock(
        side_effect=ConnectionError("connection refused")
    )
    monkeypatch.setattr(svc, "_initialized", True, raising=False)
    monkeypatch.setattr(
        svc,
        "_plugins",
        {IndexType.DOCUMENTATION: p_docs, IndexType.JOURNALS: p_journals},
        raising=False,
    )

    results, gaps = await svc.search_with_gaps("anything")

    assert len(results) == 1
    assert results[0].content == "doc hit"
    assert gaps == ["journals unavailable: failed: ConnectionError"]


@pytest.mark.asyncio
async def test_search_total_non_timeout_outage_yields_empty_results_and_gaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every index failing non-timeout populates gaps (the route's 504 trigger).

    The old behaviour returned (results=[], gaps=[]), which the route rendered
    as HTTP 200 total:0 — masking the outage.
    """
    svc = OptimalService.__new__(OptimalService)
    p_docs = _fake_plugin(IndexType.DOCUMENTATION)
    p_docs.search_with_embedding = AsyncMock(side_effect=RuntimeError("down"))
    p_journals = _fake_plugin(IndexType.JOURNALS)
    p_journals.search_with_embedding = AsyncMock(side_effect=RuntimeError("down"))
    monkeypatch.setattr(svc, "_initialized", True, raising=False)
    monkeypatch.setattr(
        svc,
        "_plugins",
        {IndexType.DOCUMENTATION: p_docs, IndexType.JOURNALS: p_journals},
        raising=False,
    )

    results, gaps = await svc.search_with_gaps("anything")

    assert results == []
    # One gap entry per index, each naming its failure.
    assert {g.split()[0] for g in gaps} == {"documentation", "journals"}
    assert all("failed: RuntimeError" in g for g in gaps)


@pytest.mark.asyncio
async def test_query_records_non_timeout_failure_in_gaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The rag_query path (_search_single_index) gets the same treatment."""
    svc = OptimalService.__new__(OptimalService)
    p_docs = _fake_plugin(IndexType.DOCUMENTATION)
    p_journals = _fake_plugin(IndexType.JOURNALS)
    p_journals.search_with_embedding = AsyncMock(side_effect=ValueError("boom"))
    monkeypatch.setattr(svc, "_initialized", True, raising=False)
    monkeypatch.setattr(
        svc,
        "_plugins",
        {IndexType.DOCUMENTATION: p_docs, IndexType.JOURNALS: p_journals},
        raising=False,
    )

    response = await svc.query("anything")

    assert len(response.gaps) == 1
    assert "journals" in response.gaps[0]
    assert "failed: ValueError" in response.gaps[0]
    # The error is still recorded in search_errors for the rag response.
    assert "failed: ValueError" in response.search_errors["journals"]


@pytest.mark.asyncio
async def test_search_still_returns_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """search() backward compat: returns list[SearchResult], not a tuple."""
    svc = OptimalService.__new__(OptimalService)
    p_docs = _fake_plugin(IndexType.DOCUMENTATION)
    monkeypatch.setattr(svc, "_initialized", True, raising=False)
    monkeypatch.setattr(
        svc,
        "_plugins",
        {IndexType.DOCUMENTATION: p_docs},
        raising=False,
    )

    result = await svc.search("anything")

    assert isinstance(result, list)


@pytest.mark.asyncio
async def test_query_includes_gaps_in_rag_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """query() passes gaps to RAGResponse when an index times out."""
    svc = OptimalService.__new__(OptimalService)
    p_docs = _fake_plugin(IndexType.DOCUMENTATION)
    p_journals = _fake_plugin(IndexType.JOURNALS)
    p_journals.search_with_embedding = AsyncMock(side_effect=TimeoutError())
    monkeypatch.setattr(svc, "_initialized", True, raising=False)
    monkeypatch.setattr(
        svc,
        "_plugins",
        {IndexType.DOCUMENTATION: p_docs, IndexType.JOURNALS: p_journals},
        raising=False,
    )

    response = await svc.query("anything")

    assert len(response.gaps) == 1
    assert "journals" in response.gaps[0]


@pytest.mark.asyncio
async def test_query_gaps_empty_when_all_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """query() returns empty gaps when all indexes complete normally."""
    svc = OptimalService.__new__(OptimalService)
    p_docs = _fake_plugin(IndexType.DOCUMENTATION)
    monkeypatch.setattr(svc, "_initialized", True, raising=False)
    monkeypatch.setattr(
        svc,
        "_plugins",
        {IndexType.DOCUMENTATION: p_docs},
        raising=False,
    )

    response = await svc.query("anything")

    assert response.gaps == []
