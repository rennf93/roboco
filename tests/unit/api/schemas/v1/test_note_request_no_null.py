"""Smoke-6: NoteRequest rejects null for decision/reflect string fields.

Original bug: minimax-m2.7 read the MCP tool schema, saw
`context: anyOf[string, null]`, and decided null was valid. Pydantic
on the route accepted it (because the field WAS `str | None`), passed
it through, and the server-side gate looped on `incomplete_input`.

Fix tightens the schema: those fields are now `str = ""`. The MCP
schema generator emits `string` (not `string | null`), and Pydantic
on the route rejects literal null with 422. Empty string is still
treated as missing at the gateway gate.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from roboco.api.schemas.v1.do import NoteRequest


def test_note_request_accepts_omitted_decision_fields() -> None:
    """A bare note (scope='note') with no structured fields builds cleanly."""
    req = NoteRequest(text="just an observation")
    assert req.context == ""
    assert req.chosen == ""
    assert req.rationale == ""
    assert req.what_done == ""
    assert req.what_learned == ""
    assert req.what_struggled == ""


def test_note_request_rejects_null_context() -> None:
    """Passing literal null for context fails Pydantic validation."""
    with pytest.raises(ValidationError) as exc_info:
        NoteRequest.model_validate({"text": "x", "scope": "decision", "context": None})
    msg = str(exc_info.value)
    assert "context" in msg.lower()


def test_note_request_rejects_null_chosen() -> None:
    with pytest.raises(ValidationError) as exc_info:
        NoteRequest.model_validate({"text": "x", "scope": "decision", "chosen": None})
    assert "chosen" in str(exc_info.value).lower()


def test_note_request_rejects_null_rationale() -> None:
    with pytest.raises(ValidationError) as exc_info:
        NoteRequest.model_validate(
            {"text": "x", "scope": "decision", "rationale": None}
        )
    assert "rationale" in str(exc_info.value).lower()


def test_note_request_rejects_null_what_done() -> None:
    with pytest.raises(ValidationError) as exc_info:
        NoteRequest.model_validate({"text": "x", "scope": "reflect", "what_done": None})
    assert "what_done" in str(exc_info.value).lower()


def test_note_request_rejects_null_what_learned() -> None:
    with pytest.raises(ValidationError) as exc_info:
        NoteRequest.model_validate(
            {"text": "x", "scope": "reflect", "what_learned": None}
        )
    assert "what_learned" in str(exc_info.value).lower()


def test_note_request_rejects_null_what_struggled() -> None:
    with pytest.raises(ValidationError) as exc_info:
        NoteRequest.model_validate(
            {"text": "x", "scope": "reflect", "what_struggled": None}
        )
    assert "what_struggled" in str(exc_info.value).lower()


def test_note_request_accepts_non_empty_strings() -> None:
    """A fully-filled decision request validates."""
    req = NoteRequest.model_validate(
        {
            "text": "Going with X",
            "scope": "decision",
            "context": "We have a choice between A and B",
            "options": [
                {"name": "A", "pros": "fast", "cons": "fragile"},
                {"name": "B", "pros": "robust", "cons": "slow"},
            ],
            "chosen": "A",
            "rationale": "speed beats robustness for this experiment",
        }
    )
    assert req.context == "We have a choice between A and B"
    assert req.chosen == "A"
    assert req.rationale.startswith("speed")
