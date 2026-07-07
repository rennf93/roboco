"""Distinct lessons must get distinct ``learning_id``s so re-ingest of one
does not delete the other's chunks.

The memory distiller emits lessons with a fixed ``Problem: …`` opening shape.
Two lessons whose first 100 chars match but whose bodies differ used to collide
on ``learning_id = f"lrn-{md5(content[:100])[:12]}"``; ``replace_on_reingest``
then routed both to the same source URI, so the second ingest's
``replace_chunks`` DELETE wiped the first lesson's chunks — silent data loss.
The fix hashes the FULL content (wider hex slice) so distinct bodies get
distinct ids and each keeps its chunks.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.services.optimal_brain.indexes.learnings import (
    LearningsIndexPlugin,
    RecordLearningParams,
)
from roboco.services.optimal_brain.text_chunker import Chunk


def _wire(plugin: object, source: str) -> AsyncMock:
    """Attach mock store/chunker/embedder; return the store mock."""
    chunk = Chunk(text="x" * 250, source=source, metadata={})
    store = AsyncMock()
    embedder = MagicMock()
    embedder.aembed_chunks = AsyncMock(return_value=[chunk])
    object.__setattr__(plugin, "_store", store)
    object.__setattr__(
        plugin, "_chunker", MagicMock(chunk_document=MagicMock(return_value=[chunk]))
    )
    object.__setattr__(plugin, "_embedder", embedder)
    object.__setattr__(plugin, "_initialized", True)
    return store


def _learning_id_from_source(source: str) -> str:
    return source.rsplit("/", 1)[-1]


@pytest.mark.asyncio
async def test_distinct_bodies_with_shared_prefix_get_distinct_ids() -> None:
    """Two lessons whose first 100 chars are identical (the fixed
    ``Problem: …`` prefix) but whose bodies differ must produce DIFFERENT
    ``learning_id``s — so both sources are distinct and ``replace_chunks``
    of the second does NOT delete the first's chunks."""
    prefix = "Problem: " + "x" * 91  # exactly 100 chars
    content_a = prefix + "\nApproach: do A. " + "A" * 300
    content_b = prefix + "\nApproach: do B. " + "B" * 300
    assert content_a[:100] == content_b[:100]
    assert content_a != content_b

    plugin = LearningsIndexPlugin()
    store = _wire(plugin, "roboco://learnings/lrn-x")
    store.replace_chunks = AsyncMock()

    await plugin.record_learning(
        RecordLearningParams(content=content_a, category="error_handling")
    )
    await plugin.record_learning(
        RecordLearningParams(content=content_b, category="error_handling")
    )

    _EXPECTED_INGESTS = 2
    assert store.replace_chunks.await_count == _EXPECTED_INGESTS
    sources = [call.args[0] for call in store.replace_chunks.await_args_list]
    id_a = _learning_id_from_source(sources[0])
    id_b = _learning_id_from_source(sources[1])
    assert id_a != id_b, (
        f"distinct lessons collapsed to the same learning_id {id_a!r} "
        "— second ingest would delete the first's chunks"
    )
    assert sources[0] != sources[1]
    # The second ingest must NOT delete the first's source: replace_chunks
    # deletes only by its own source, and the two sources differ.
    store.delete_by_source.assert_not_awaited()
