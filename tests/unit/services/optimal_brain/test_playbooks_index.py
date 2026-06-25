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
