"""
Vault Notes Index Plugin

Indexes human-authored Obsidian vault notes (the CEO's own writing under
``vault_kb_dirs``, default ``RoboCo/Notes``) so the fleet can retrieve them
alongside learnings/playbooks. Mirrors the PlaybooksIndexPlugin shape; the
embed + pgvector ingest/search machinery is inherited from BaseIndexPlugin.

Never covers Tasks/Journals/A2A/Agents (already DB-indexed as first-class
corpora) or the intake Inbox (config-load validation rejects the overlap) —
enforced by ``VaultKBEngine``'s dir allowlist, not here.
"""

from typing import Any

from roboco.models.optimal import IndexType, SearchResult
from roboco.services.optimal_brain.indexes.base import BaseIndexPlugin, IngestResult


class VaultNotesIndexPlugin(BaseIndexPlugin):
    """Index + search human-authored vault notes."""

    @property
    def index_type(self) -> IndexType:
        return IndexType.VAULT_NOTES

    def prepare_metadata(self, content: str, **kwargs: Any) -> dict[str, Any]:
        """Prepare metadata for a vault note (path is the stable identity)."""
        del content  # Unused - metadata comes from kwargs
        return {
            "type": "vault_note",
            "source": "vault",
            "path": str(kwargs.get("path", "")),
            "title": str(kwargs.get("title", "")),
            "content_hash": str(kwargs.get("content_hash", "")),
        }

    def build_source_uri(self, doc_id: str | None = None, **kwargs: Any) -> str | None:
        """Build the source URI for a vault note (``doc_id`` is its vault-relative
        path), or None if missing."""
        del kwargs  # Unused - URI uses doc_id only
        return f"vault://{doc_id}" if doc_id else None

    async def index_note(
        self, *, path: str, title: str, content: str, content_hash: str
    ) -> IngestResult:
        """Embed one vault note's body, keyed by its vault-relative path."""
        return await self.ingest(
            content=content,
            doc_id=path,
            path=path,
            title=title,
            content_hash=content_hash,
        )

    async def delete_note(self, path: str) -> None:
        """Remove a deleted/moved note's embedded chunks from the vector store.

        Idempotent: the store's ``delete_by_source`` no-ops when no chunks
        match the source URI.
        """
        source = self.build_source_uri(doc_id=path)
        if not source:
            return
        await self._require_store.delete_by_source(source)

    async def search_notes(self, query: str, top_k: int = 10) -> list[SearchResult]:
        """Search vault notes."""
        outcome = await self.search(query=query, top_k=top_k)
        return outcome.results
