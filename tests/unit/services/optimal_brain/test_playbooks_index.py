"""PlaybooksIndexPlugin — index_type + pure metadata/URI methods.

Mirrors the other index-plugin unit tests (instantiate via __new__, exercise the
pure methods). The embed + pgvector ingest/search path is inherited from
BaseIndexPlugin (shared, proven by the other 8 plugins) and runs live.
"""

from __future__ import annotations

from roboco.models.optimal import IndexType
from roboco.services.optimal_brain.indexes.playbooks import PlaybooksIndexPlugin


def _plugin() -> PlaybooksIndexPlugin:
    return PlaybooksIndexPlugin.__new__(PlaybooksIndexPlugin)


def test_index_type_is_playbooks() -> None:
    assert _plugin().index_type == IndexType.PLAYBOOKS


def test_prepare_metadata_marks_approved_with_routing_fields() -> None:
    md = _plugin().prepare_metadata(
        "content", playbook_id="pb-1", team="backend", scope="org", tags=["retry"]
    )
    assert md["type"] == "playbook"
    assert md["playbook_id"] == "pb-1"
    assert md["team"] == "backend"
    assert md["scope"] == "org"
    assert md["tags"] == ["retry"]
    # Only approved playbooks are ever indexed — the metadata says so.
    assert md["status"] == "approved"


def test_build_source_uri_with_id() -> None:
    assert _plugin().build_source_uri(doc_id="pb-1") == "roboco://playbooks/pb-1"


def test_build_source_uri_none_when_missing() -> None:
    assert _plugin().build_source_uri(doc_id=None) is None


def test_delete_playbook_removes_its_chunks_by_source() -> None:
    """F011: deleting a playbook removes its embedded chunks from the vector
    store by the playbook's source URI (idempotent — no-op if absent). A
    rejected/archived playbook must not stay retrievable in the PLAYBOOKS index."""
    from unittest.mock import AsyncMock, MagicMock

    plugin = PlaybooksIndexPlugin.__new__(PlaybooksIndexPlugin)
    store = MagicMock()
    store.delete_by_source = AsyncMock(return_value=None)
    # Bypass the initialized-guard property so the unit test doesn't need a
    # live pgvector store.
    object.__setattr__(plugin, "_initialized", True)
    object.__setattr__(plugin, "_store", store)

    import asyncio

    asyncio.run(plugin.delete_playbook("pb-42"))

    store.delete_by_source.assert_awaited_once_with("roboco://playbooks/pb-42")
