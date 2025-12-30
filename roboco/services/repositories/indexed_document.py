"""
Indexed Document Repository

Repository for managing indexed documents in the knowledge base.
"""

import hashlib
from typing import Any

from sqlalchemy import select

from roboco.db.tables import IndexedDocumentTable
from roboco.services.repositories.base import BaseRepository


class IndexedDocumentRepository(BaseRepository[IndexedDocumentTable]):
    """Repository for indexed document operations."""

    model = IndexedDocumentTable
    model_name = "IndexedDocument"

    async def upsert_batch(
        self,
        index_type: str,
        documents: list[dict[str, Any]],
    ) -> int:
        """
        Upsert multiple documents in a single transaction.

        Args:
            index_type: The index type (code, documentation, standards, etc.)
            documents: List of document dicts with keys:
                - source: Source path/URI (required)
                - title: Document title
                - preview: Content preview (truncated to 500 chars)
                - metadata: Additional metadata dict

        Returns:
            Number of documents upserted
        """
        if not documents:
            return 0

        count = 0
        for doc_info in documents:
            source = doc_info["source"]
            title = doc_info.get("title")
            preview = doc_info.get("preview")
            metadata = doc_info.get("metadata")

            source_hash = hashlib.sha256(source.encode()).hexdigest()

            existing = await self.session.execute(
                select(IndexedDocumentTable).where(
                    IndexedDocumentTable.index_type == index_type,
                    IndexedDocumentTable.source_hash == source_hash,
                )
            )
            doc = existing.scalar_one_or_none()

            if doc:
                # Update existing
                if title:
                    doc.title = title
                if preview:
                    doc.preview = preview[:500]
                if metadata:
                    doc.extra_data = {**(doc.extra_data or {}), **metadata}
            else:
                # Insert new
                doc = IndexedDocumentTable(
                    index_type=index_type,
                    source=source,
                    source_hash=source_hash,
                    title=title,
                    preview=preview[:500] if preview else None,
                    extra_data=metadata or {},
                )
                self.session.add(doc)

            count += 1

        await self.session.flush()
        return count

    async def get_by_index_type(
        self,
        index_type: str,
        limit: int = 100,
        offset: int = 0,
    ) -> list[IndexedDocumentTable]:
        """Get all documents for an index type."""
        return await self.find_by(
            IndexedDocumentTable.index_type == index_type,
            limit=limit,
            offset=offset,
        )

    async def count_by_index_type(self, index_type: str) -> int:
        """Count documents in an index type."""
        return await self.count(IndexedDocumentTable.index_type == index_type)

    async def delete_by_index_type(self, index_type: str) -> int:
        """Delete all documents for an index type."""
        docs = await self.get_by_index_type(index_type, limit=10000)
        for doc in docs:
            await self.session.delete(doc)
        await self.session.flush()
        return len(docs)
