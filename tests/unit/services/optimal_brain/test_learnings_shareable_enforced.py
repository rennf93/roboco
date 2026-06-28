"""F054: the LEARNINGS index must not leak private (shareable=False) entries
through ANY shared retrieval path.

A private LEARNING journal entry is recorded into the LEARNINGS index with
``shareable=False`` (journal.py records it for completeness but it is never
meant to surface to other agents). The shared retrieval paths all reach the
plugin's retrieval with no ``include_private`` opt-in:

- ``OptimalService.search`` (used by the briefing / ``similar_memory``) calls
  ``search_with_embedding`` directly with no filters.
- ``search_learnings`` (shareable_only=True, the default) and the
  ``get_learnings_by_category`` / ``get_learnings_by_role`` /
  ``get_team_learnings`` cross-agent views call ``search`` with a filters dict
  that does NOT carry a ``shareable`` key.

The base ``_citations_to_results`` only filters when a ``shareable`` filter is
present, so a ``shareable=False`` chunk sails through into another agent's
briefing — a private reflection leaked across the cross-agent corpus.

The fix: the LEARNINGS plugin forces ``shareable=True`` on retrieval unless the
caller explicitly opts into the private view via ``include_private=True`` (the
``search_learnings(shareable_only=False)`` audit/admin path). An empty filters
dict does NOT opt out — shareable is the safe default on every shared path.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from roboco.models.optimal import IndexType
from roboco.services.optimal_brain.indexes.learnings import LearningsIndexPlugin
from roboco.services.optimal_brain.text_chunker import Citation


def _plugin(store: Any) -> LearningsIndexPlugin:
    plugin = LearningsIndexPlugin()
    object.__setattr__(plugin, "_store", store)
    object.__setattr__(plugin, "_initialized", True)
    return plugin


def _cite(text: str, shareable: bool, **extra: Any) -> Citation:
    metadata: dict[str, Any] = {"shareable": shareable}
    metadata.update(extra)
    return Citation(
        chunk=text,
        source=f"roboco://learnings/{text.split(maxsplit=1)[0]}",
        score=0.9,
        metadata=metadata,
    )


def _store_with(
    public: str = "public lesson A", private: str = "private reflection B"
) -> AsyncMock:
    store = AsyncMock()
    store.hybrid_search = AsyncMock(
        return_value=[
            _cite(public, shareable=True),
            _cite(private, shareable=False),
        ]
    )
    return store


@pytest.mark.asyncio
async def test_shared_no_filters_path_excludes_private_learnings() -> None:
    """``search_with_embedding`` with no filters (the OptimalService.search /
    briefing path) must NOT return a shareable=False entry — a private learning
    must not leak across the cross-agent corpus."""
    plugin = _plugin(_store_with())

    outcome = await plugin.search_with_embedding(
        [0.1, 0.2, 0.3], "some query", top_k=4
    )  # no filters, no include_private → shared path

    assert outcome.success
    chunks = [r.content for r in outcome.results]
    assert "public lesson A" in chunks
    assert "private reflection B" not in chunks  # private learning does not leak


@pytest.mark.asyncio
async def test_empty_filters_dict_still_enforces_shareable() -> None:
    """An empty ``filters={}`` does NOT opt out of the shareable default — the
    safe default on every shared path is shareable-only. (The previous
    None-vs-dict rule let a bare ``{}`` leak private; the fix makes shareable
    the default unless ``include_private=True``.)"""
    plugin = _plugin(_store_with())

    outcome = await plugin.search_with_embedding(
        [0.1, 0.2, 0.3], "some query", top_k=4, filters={}
    )

    chunks = [r.content for r in outcome.results]
    assert "public lesson A" in chunks
    assert "private reflection B" not in chunks


@pytest.mark.asyncio
async def test_explicit_shareable_true_filter_excludes_private() -> None:
    """A caller passing ``filters={"shareable": True}`` explicitly still gets
    only shareable entries (the search_learnings(shareable_only=True) path)."""
    plugin = _plugin(_store_with())

    outcome = await plugin.search_with_embedding(
        [0.1, 0.2, 0.3], "some query", top_k=4, filters={"shareable": True}
    )

    chunks = [r.content for r in outcome.results]
    assert "public lesson A" in chunks
    assert "private reflection B" not in chunks


@pytest.mark.asyncio
async def test_include_private_opt_in_returns_private_learnings() -> None:
    """The ``search_learnings(shareable_only=False)`` audit/admin path threads
    ``include_private=True`` — that is the ONLY way to surface private
    learnings. The plugin must respect it (no shareable filter applied)."""
    plugin = _plugin(_store_with())

    outcome = await plugin.search_with_embedding(
        [0.1, 0.2, 0.3], "some query", top_k=4, include_private=True
    )

    chunks = [r.content for r in outcome.results]
    assert "public lesson A" in chunks
    assert "private reflection B" in chunks  # opt-in honored


@pytest.mark.asyncio
async def test_get_learnings_by_category_excludes_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``get_learnings_by_category`` is a cross-agent shared view — it must NOT
    leak private learnings. It calls ``search`` with ``filters={"category": X}``
    and no ``shareable`` key; the plugin must still force shareable."""
    store = AsyncMock()
    store.hybrid_search = AsyncMock(
        return_value=[
            _cite("public lesson A", shareable=True, category="testing"),
            _cite("private reflection B", shareable=False, category="testing"),
        ]
    )
    plugin = _plugin(store)
    # ``search`` embeds the query via the store; the fake store returns the two
    # citations from hybrid_search regardless of the embedding, so we can assert
    # the shareable filter is applied post-fetch.
    monkeypatch.setattr(
        plugin, "_compute_query_embedding", AsyncMock(return_value=[0.1, 0.2, 0.3])
    )

    results = await plugin.get_learnings_by_category("testing", top_k=4)

    chunks = [r.content for r in results]
    assert "public lesson A" in chunks
    assert "private reflection B" not in chunks


@pytest.mark.asyncio
async def test_search_learnings_shareable_only_false_returns_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``search_learnings(shareable_only=False)`` is the documented opt-in to
    the private/admin view — it must thread ``include_private=True`` and surface
    private learnings (regression guard for the opt-out wiring)."""
    plugin = _plugin(_store_with())
    monkeypatch.setattr(
        plugin, "_compute_query_embedding", AsyncMock(return_value=[0.1, 0.2, 0.3])
    )

    results = await plugin.search_learnings("query", shareable_only=False, top_k=4)

    chunks = [r.content for r in results]
    assert "public lesson A" in chunks
    assert "private reflection B" in chunks


@pytest.mark.asyncio
async def test_search_learnings_shareable_only_true_excludes_private(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``search_learnings(shareable_only=True)`` (the default every production
    caller uses) must NOT leak private learnings."""
    plugin = _plugin(_store_with())
    monkeypatch.setattr(
        plugin, "_compute_query_embedding", AsyncMock(return_value=[0.1, 0.2, 0.3])
    )

    results = await plugin.search_learnings("query", shareable_only=True, top_k=4)

    chunks = [r.content for r in results]
    assert "public lesson A" in chunks
    assert "private reflection B" not in chunks


def test_learnings_index_type_is_learnings() -> None:
    """Sanity: the plugin we're testing is the LEARNINGS index."""
    assert LearningsIndexPlugin().index_type == IndexType.LEARNINGS
