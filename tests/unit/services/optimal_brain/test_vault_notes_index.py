"""VaultNotesIndexPlugin — index_type + pure metadata/URI methods.

Mirrors the other index-plugin unit tests (instantiate via __new__, exercise
the pure methods). The embed + pgvector ingest/search path is inherited from
BaseIndexPlugin (shared, proven by the other index plugins) and runs live.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from roboco.models.optimal import IndexType
from roboco.services.optimal_brain.indexes.base import IndexConfig
from roboco.services.optimal_brain.indexes.vault_notes import VaultNotesIndexPlugin


def _plugin() -> VaultNotesIndexPlugin:
    return VaultNotesIndexPlugin.__new__(VaultNotesIndexPlugin)


def test_index_type_is_vault_notes() -> None:
    assert _plugin().index_type == IndexType.VAULT_NOTES


def test_prepare_metadata_carries_path_and_hash() -> None:
    md = _plugin().prepare_metadata(
        "content", path="RoboCo/Notes/a.md", title="A", content_hash="abc123"
    )
    assert md["type"] == "vault_note"
    assert md["source"] == "vault"
    assert md["path"] == "RoboCo/Notes/a.md"
    assert md["title"] == "A"
    assert md["content_hash"] == "abc123"


def test_build_source_uri_with_path() -> None:
    assert (
        _plugin().build_source_uri(doc_id="RoboCo/Notes/a.md")
        == "vault://RoboCo/Notes/a.md"
    )


def test_build_source_uri_none_when_missing() -> None:
    assert _plugin().build_source_uri(doc_id=None) is None


_SHORT_NOTE_FLOOR = 40  # journals-style floor; the global default is 200


def test_min_chunk_length_floor_allows_short_notes() -> None:
    """CEO vault notes are often a few short lines — the global 200-char
    quality floor would discard them all as garbage (the exact failure the
    journals/learnings floors fixed). Same floor as journals."""
    vault_floor = IndexConfig.from_settings(IndexType.VAULT_NOTES).min_chunk_length
    journal_floor = IndexConfig.from_settings(IndexType.JOURNALS).min_chunk_length
    assert vault_floor == journal_floor == _SHORT_NOTE_FLOOR


def test_delete_note_removes_its_chunks_by_source() -> None:
    """Deleting a note removes its embedded chunks from the vector store by
    the note's source URI (idempotent — no-op if absent). A deleted/moved
    note must not stay retrievable in the VAULT_NOTES index."""
    plugin = VaultNotesIndexPlugin.__new__(VaultNotesIndexPlugin)
    store = MagicMock()
    store.delete_by_source = AsyncMock(return_value=None)
    object.__setattr__(plugin, "_initialized", True)
    object.__setattr__(plugin, "_store", store)

    asyncio.run(plugin.delete_note("RoboCo/Notes/a.md"))

    store.delete_by_source.assert_awaited_once_with("vault://RoboCo/Notes/a.md")
