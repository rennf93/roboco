"""
Indexed Document Repository

Repository for managing indexed documents in the knowledge base.
"""

import hashlib
from typing import Any

from sqlalchemy import cast, delete, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert

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

        # Dedupe within the batch (last wins) — index_type is constant here, so
        # source_hash is the key; ON CONFLICT DO UPDATE can't touch the same row
        # twice in one statement.
        by_hash: dict[str, dict[str, Any]] = {}
        for doc_info in documents:
            source = doc_info["source"]
            preview = doc_info.get("preview")
            source_hash = hashlib.sha256(source.encode()).hexdigest()
            by_hash[source_hash] = {
                "index_type": index_type,
                "source": source,
                "source_hash": source_hash,
                "title": doc_info.get("title"),
                "preview": preview[:500] if preview else None,
                "extra_data": doc_info.get("metadata") or {},
            }
        rows = list(by_hash.values())

        # Atomic upsert. The prior check-then-insert raced under concurrent
        # indexing: two callers both saw no row and both inserted -> the second
        # violated uq_indexed_doc_source, poisoning its transaction (and, in CI,
        # segfaulting on the failed-connection checkin).
        stmt = pg_insert(IndexedDocumentTable).values(rows)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_indexed_doc_source",
            set_={
                # keep the existing value when the new one is empty (matches the
                # old `if title:` / `if preview:` guards)
                "title": func.coalesce(stmt.excluded.title, IndexedDocumentTable.title),
                "preview": func.coalesce(
                    stmt.excluded.preview, IndexedDocumentTable.preview
                ),
                # merge metadata (existing || new, new wins) via jsonb concat
                "extra_data": cast(
                    cast(IndexedDocumentTable.extra_data, JSONB).op("||")(
                        cast(stmt.excluded.extra_data, JSONB)
                    ),
                    IndexedDocumentTable.extra_data.type,
                ),
            },
        )
        await self.session.execute(stmt)
        # The Core upsert bypasses the ORM identity map, so any row already
        # loaded in this session is now stale — expire so a same-session read
        # reflects the merged DB row, not the cached object.
        self.session.expire_all()
        return len(rows)

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

    async def delete_by_source(self, index_type: str, source: str) -> bool:
        """Delete the indexed-document tracking row for one source URI.

        Used by de-index paths (e.g. a rejected/archived playbook) to drop the
        tracking row whose chunks the vector store has already removed by the
        same source. Idempotent: returns ``False`` (nothing deleted) when no
        tracking row exists, ``True`` when one was removed.
        """
        source_hash = hashlib.sha256(source.encode()).hexdigest()
        result = await self.session.execute(
            delete(IndexedDocumentTable).where(
                IndexedDocumentTable.index_type == index_type,
                IndexedDocumentTable.source_hash == source_hash,
            )
        )
        await self.session.flush()
        rowcount: int = getattr(result, "rowcount", 0) or 0
        return rowcount > 0

    async def delete_by_index_type(self, index_type: str) -> int:
        """Delete all documents for an index type."""
        docs = await self.get_by_index_type(index_type, limit=10000)
        for doc in docs:
            await self.session.delete(doc)
        await self.session.flush()
        return len(docs)
