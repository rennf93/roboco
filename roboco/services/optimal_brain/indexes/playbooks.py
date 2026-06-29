"""
Playbooks Index Plugin

Indexes curated, Auditor-approved playbooks (when-to-use + procedure) so they are
retrievable and auto-suggested in agent briefings. Only APPROVED playbooks are
indexed — the metadata records ``status: approved`` accordingly. Mirrors the
LearningsIndexPlugin shape; the embed + pgvector ingest/search machinery is
inherited from BaseIndexPlugin.
"""

from dataclasses import dataclass, field
from typing import Any

from roboco.models.optimal import IndexType, SearchResult
from roboco.services.optimal_brain.indexes.base import BaseIndexPlugin, IngestResult


@dataclass
class IndexPlaybookParams:
    """Parameters for indexing an approved playbook."""

    playbook_id: str
    title: str
    problem: str
    procedure: str
    tags: list[str] = field(default_factory=list)
    team: str | None = None
    scope: str = "org"


class PlaybooksIndexPlugin(BaseIndexPlugin):
    """Index + search curated, approved playbooks."""

    @property
    def index_type(self) -> IndexType:
        return IndexType.PLAYBOOKS

    def prepare_metadata(self, content: str, **kwargs: Any) -> dict[str, Any]:
        """Prepare metadata for a playbook (only approved ones are indexed)."""
        del content  # Unused - metadata comes from kwargs
        return {
            "type": "playbook",
            "playbook_id": str(kwargs.get("playbook_id", "")),
            "team": kwargs.get("team") or "",
            "scope": kwargs.get("scope", "org"),
            "tags": kwargs.get("tags", []),
            "status": "approved",
        }

    def build_source_uri(self, doc_id: str | None = None, **kwargs: Any) -> str | None:
        """Build the source URI for a playbook, or None when its id is missing."""
        del kwargs  # Unused - URI uses doc_id only
        return f"roboco://playbooks/{doc_id}" if doc_id else None

    async def index_playbook(self, params: IndexPlaybookParams) -> IngestResult:
        """Embed an approved playbook's title + when-to-use + procedure."""
        parts = [
            params.title,
            f"\nWhen to use:\n{params.problem}",
            f"\nProcedure:\n{params.procedure}",
        ]
        if params.tags:
            parts.append(f"\nTags: {', '.join(params.tags)}")
        return await self.ingest(
            content="\n".join(parts),
            doc_id=params.playbook_id,
            playbook_id=params.playbook_id,
            team=params.team,
            scope=params.scope,
            tags=params.tags,
        )

    async def delete_playbook(self, playbook_id: str) -> None:
        """Remove a playbook's embedded chunks from the vector store.

        Used when a playbook is rejected/archived after it was approved+indexed,
        so it stops surfacing in agent briefings as a stale, no-longer-canonical
        procedure. Idempotent: the store's ``delete_by_source`` no-ops when no
        chunks match the source URI.
        """
        source = self.build_source_uri(doc_id=playbook_id)
        if not source:
            return
        await self._require_store.delete_by_source(source)

    async def search_playbooks(
        self,
        query: str,
        team: str | None = None,
        top_k: int = 10,
    ) -> list[SearchResult]:
        """Search approved playbooks, optionally scoped to a team."""
        filters: dict[str, Any] = {}
        if team:
            filters["team"] = team
        outcome = await self.search(query=query, top_k=top_k, filters=filters)
        return outcome.results
