"""#169: i_documented must persist DocRef-shaped dicts, not bare strings.

Smoke-15: be-doc i_documented(files=["README.md"]) → choreographer
doc.py stamped `existing.documents = files` (list[str]). Task.documents
is list[DocRef] persisted as dicts; readers do DocRef(**d) / d["path"]
and the indexer does d.get("path"). A bare string then 500'd
GET /docs (`TypeError: DocRef() argument after ** must be a mapping,
not str`) and would AttributeError the indexer. Fix: build proper
DocRef dicts at the source (_doc_refs_for) + defensively coerce on read
(_coerce_doc_ref) so legacy/corrupted rows don't explode.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from roboco.models.task import DocRef
from roboco.services.docs import _coerce_doc_ref
from roboco.services.gateway.choreographer.doc import _doc_refs_for


def test_doc_refs_for_builds_valid_docref_dicts() -> None:
    """The stamped elements are dicts that survive DocRef(**d) and the
    indexer's d.get('path') — the two paths that broke in smoke-15."""
    agent = uuid4()
    inputs = ["README.md", "docs/api/endpoints.md"]
    out = _doc_refs_for(inputs, agent)

    assert len(out) == len(inputs)
    for d, expected_path, expected_title in (
        (out[0], "README.md", "README.md"),
        (out[1], "docs/api/endpoints.md", "endpoints.md"),
    ):
        assert isinstance(d, dict), d
        # Indexer path (roboco/services/task.py:_index_docs_background).
        assert d.get("path") == expected_path
        # list_docs / _get_existing_doc_ref path.
        ref = DocRef(**d)
        assert ref.path == expected_path
        assert ref.title == expected_title
        assert ref.doc_type == "doc"
        assert ref.created_by == str(agent)


def test_doc_refs_for_empty_list() -> None:
    assert _doc_refs_for([], uuid4()) == []


def test_coerce_doc_ref_passthrough_docref() -> None:
    ref = DocRef(path="a.md", title="a.md", doc_type="doc")
    assert _coerce_doc_ref(ref) is ref


def test_coerce_doc_ref_from_dict() -> None:
    ref = DocRef(path="a.md", title="a.md", doc_type="doc")
    out = _coerce_doc_ref(ref.model_dump())
    assert isinstance(out, DocRef)
    assert out.path == "a.md"


def test_coerce_doc_ref_from_bare_string_is_the_169_fix() -> None:
    """The exact smoke-15 corruption: a bare path string must NOT raise
    (previously `DocRef(**"README.md")` → TypeError → 500)."""
    out = _coerce_doc_ref("README.md")
    assert isinstance(out, DocRef)
    assert out.path == "README.md"
    assert out.title == "README.md"
    assert out.doc_type == "doc"


def test_coerce_doc_ref_rejects_unsupported_type() -> None:
    with pytest.raises(TypeError, match=r"unsupported Task\.documents element"):
        _coerce_doc_ref(123)


def test_source_output_round_trips_through_read_coercion() -> None:
    """End-to-end invariant: what _doc_refs_for writes is exactly what
    _coerce_doc_ref reads back without loss."""
    agent = uuid4()
    stamped = _doc_refs_for(["README.md"], agent)
    refs = [_coerce_doc_ref(d) for d in stamped]
    assert [r.path for r in refs] == ["README.md"]
    assert refs[0].created_by == str(agent)
