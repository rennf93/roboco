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
    assert count == 2


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
async def test_get_by_index_type(repo: IndexedDocumentRepository) -> None:
    await repo.upsert_batch(
        "documentation",
        [{"source": "doc1.md", "title": "T1"}, {"source": "doc2.md", "title": "T2"}],
    )
    rows = await repo.get_by_index_type("documentation")
    assert len(rows) >= 2


@pytest.mark.asyncio
async def test_count_by_index_type(repo: IndexedDocumentRepository) -> None:
    await repo.upsert_batch("standards", [{"source": "s1.md", "title": "S1"}])
    count = await repo.count_by_index_type("standards")
    assert count >= 1


@pytest.mark.asyncio
async def test_delete_by_index_type(repo: IndexedDocumentRepository) -> None:
    await repo.upsert_batch(
        "to-delete",
        [{"source": "x.md", "title": "X"}, {"source": "y.md", "title": "Y"}],
    )
    deleted = await repo.delete_by_index_type("to-delete")
    assert deleted >= 2
    assert await repo.count_by_index_type("to-delete") == 0


@pytest.mark.asyncio
async def test_upsert_batch_truncates_long_preview(
    repo: IndexedDocumentRepository,
) -> None:
    """Preview is truncated to 500 chars."""
    docs = [{"source": "long.md", "title": "Long", "preview": "a" * 1000}]
    await repo.upsert_batch("code", docs)
    rows = await repo.get_by_index_type("code")
    matching = [r for r in rows if r.source == "long.md"]
    assert matching
    assert len(matching[0].preview) <= 500
