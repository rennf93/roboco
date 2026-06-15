"""VectorStore must decode jsonb metadata returned by asyncpg as a string.

asyncpg returns ``jsonb`` columns as a JSON *string* (no codec on the pool), so
``dict(value)`` iterated characters and raised "dictionary update sequence
element #0 has length 1; 2 is required" — which made every KB search fail at the
row-mapping step once the schema (migration 030) let the query reach rows.
"""

from __future__ import annotations

from roboco.services.optimal_brain.vector_store import _as_dict


def test_decodes_jsonb_string() -> None:
    # The bug case: asyncpg hands back a JSON string, not a dict.
    assert _as_dict('{"agent": "be-dev-1", "type": "journal"}') == {
        "agent": "be-dev-1",
        "type": "journal",
    }


def test_passes_through_mapping() -> None:
    assert _as_dict({"k": "v"}) == {"k": "v"}


def test_none_and_empty_become_empty_dict() -> None:
    assert _as_dict(None) == {}
    assert _as_dict("") == {}


def test_non_object_json_becomes_empty_dict() -> None:
    # A bare JSON scalar/array is not a metadata mapping.
    assert _as_dict("[1, 2, 3]") == {}
    assert _as_dict('"just a string"') == {}
