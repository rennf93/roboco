"""IndexedDocumentRepository coverage."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from roboco.services.repositories.indexed_document import (
    IndexedDocumentRepository,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession


@pytest_asyncio.fixture
async def repo(db_session: AsyncSession) -> AsyncIterator[IndexedDocumentRepository]:
    yield IndexedDocumentRepository(db_session)


@pytest.mark.asyncio
async def test_upsert_batch_empty_returns_zero(
    repo: IndexedDocumentRepository,
) -> None:
    assert await repo.upsert_batch("code", []) == 0


@pytest.mark.asyncio
async def test_upsert_batch_inserts_new(
    repo: IndexedDocumentRepository,
) -> None:
    docs = [
        {"source": "a/file1.py", "title": "F1", "preview": "x" * 600},
        {"source": "a/file2.py", "title": "F2", "preview": "y"},
    ]
    count = await repo.upsert_batch("code", docs)
    _DOCS = 2
    assert count == _DOCS


@pytest.mark.asyncio
async def test_upsert_batch_updates_existing(
    repo: IndexedDocumentRepository,
) -> None:
    docs = [{"source": "same.py", "title": "Original"}]
    await repo.upsert_batch("code", docs)
    docs[0]["title"] = "Updated"
    count = await repo.upsert_batch("code", docs)
    assert count == 1


@pytest.mark.asyncio
async def test_upsert_batch_dedupes_within_batch_and_merges(
    repo: IndexedDocumentRepository,
) -> None:
    # A batch containing the SAME source twice must not raise
    # ("ON CONFLICT cannot affect row a second time") — the atomic upsert
    # dedupes within the batch (last wins) and merges metadata.
    count = await repo.upsert_batch(
        "learnings",
        [
            {"source": "dup", "title": "First", "metadata": {"a": 1}},
            {"source": "dup", "title": "Second", "metadata": {"b": 2}},
        ],
    )
    assert count == 1
    rows = await repo.get_by_index_type("learnings")
    assert len(rows) == 1
    assert rows[0].title == "Second"
    # re-upsert the same source (the race scenario, sequentially): keeps the
    # existing title on an empty new one, and merges metadata.
    await repo.upsert_batch("learnings", [{"source": "dup", "metadata": {"c": 3}}])
    rows = await repo.get_by_index_type("learnings")
    assert len(rows) == 1
    assert rows[0].title == "Second"
    assert rows[0].extra_data == {"b": 2, "c": 3}


@pytest.mark.asyncio
async def test_get_by_index_type(repo: IndexedDocumentRepository) -> None:
    await repo.upsert_batch(
        "documentation",
        [{"source": "doc1.md", "title": "T1"}, {"source": "doc2.md", "title": "T2"}],
    )
    _DOCS = 2
    rows = await repo.get_by_index_type("documentation")
    assert len(rows) >= _DOCS


@pytest.mark.asyncio
async def test_count_by_index_type(repo: IndexedDocumentRepository) -> None:
    await repo.upsert_batch("standards", [{"source": "s1.md", "title": "S1"}])
    count = await repo.count_by_index_type("standards")
    assert count >= 1


@pytest.mark.asyncio
async def test_delete_by_index_type(repo: IndexedDocumentRepository) -> None:
    _DOCS = 2
    await repo.upsert_batch(
        "to-delete",
        [{"source": "x.md", "title": "X"}, {"source": "y.md", "title": "Y"}],
    )
    deleted = await repo.delete_by_index_type("to-delete")
    assert deleted >= _DOCS
    assert await repo.count_by_index_type("to-delete") == 0


@pytest.mark.asyncio
async def test_upsert_batch_truncates_long_preview(
    repo: IndexedDocumentRepository,
) -> None:
    """Preview is truncated to 500 chars."""
    docs = [{"source": "long.md", "title": "Long", "preview": "a" * 1000}]
    await repo.upsert_batch("code", docs)
    _PREVIEW_MAX = 500
    rows = await repo.get_by_index_type("code")
    matching = [r for r in rows if r.source == "long.md"]
    assert matching
    preview = matching[0].preview
    assert preview is not None
    assert len(preview) <= _PREVIEW_MAX


@pytest.mark.asyncio
async def test_upsert_batch_updates_preview_and_metadata(
    repo: IndexedDocumentRepository,
) -> None:
    """Lines 66, 68: update path with preview + metadata fields."""
    # Insert first.
    await repo.upsert_batch(
        "code",
        [{"source": "metadoc.py", "title": "Old", "metadata": {"a": 1}}],
    )
    # Update with preview + metadata.
    await repo.upsert_batch(
        "code",
        [
            {
                "source": "metadoc.py",
                "title": "New",
                "preview": "fresh content",
                "metadata": {"b": 2},
            }
        ],
    )
    _A_VALUE = 1
    _B_VALUE = 2
    rows = await repo.get_by_index_type("code")
    matching = [r for r in rows if r.source == "metadoc.py"]
    assert matching
    assert matching[0].preview == "fresh content"
    # Metadata is merged.
    assert matching[0].extra_data["a"] == _A_VALUE
    assert matching[0].extra_data["b"] == _B_VALUE
