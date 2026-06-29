"""Smoke-6: NoteRequest rejects null for decision/reflect string fields.

Original bug: minimax-m3 read the MCP tool schema, saw
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


# ---------------------------------------------------------------------------
# Issue #15: list-typed fields tolerate a lone scalar (string or, for
# options, a dict). Pre-coercion these 422'd at the route and the agent's
# retry loop tripped the do-server circuit breaker.
# ---------------------------------------------------------------------------


def test_note_request_coerces_string_consequences_to_list() -> None:
    """A single string for consequences is wrapped into a one-element list."""
    req = NoteRequest.model_validate(
        {"text": "x", "scope": "decision", "consequences": "we lose durability"}
    )
    assert req.consequences == ["we lose durability"]


def test_note_request_coerces_string_next_steps_to_list() -> None:
    """A single string for next_steps is wrapped into a one-element list."""
    req = NoteRequest.model_validate(
        {"text": "x", "scope": "reflect", "next_steps": "wait for QA"}
    )
    assert req.next_steps == ["wait for QA"]


def test_note_request_coerces_string_where_to_look_to_list() -> None:
    """A single string for where_to_look is wrapped into a one-element list,
    mirroring consequences/next_steps, so a lone scalar does not 422 the route."""
    req = NoteRequest.model_validate(
        {"text": "x", "scope": "handoff", "where_to_look": "src/api/auth.py"}
    )
    assert req.where_to_look == ["src/api/auth.py"]


def test_note_request_coerces_single_option_dict_to_list() -> None:
    """A single option dict (not wrapped in a list) is wrapped into a list."""
    req = NoteRequest.model_validate(
        {
            "text": "x",
            "scope": "decision",
            "options": {"name": "redis", "pros": "fast", "cons": "ephemeral"},
        }
    )
    assert req.options == [{"name": "redis", "pros": "fast", "cons": "ephemeral"}]


def test_note_request_list_fields_pass_through_unchanged() -> None:
    """Already-list values are not re-wrapped."""
    req = NoteRequest.model_validate(
        {
            "text": "x",
            "scope": "reflect",
            "next_steps": ["step one", "step two"],
        }
    )
    assert req.next_steps == ["step one", "step two"]


def test_note_request_list_fields_accept_none() -> None:
    """Omitted list fields stay None (default), not coerced."""
    req = NoteRequest.model_validate({"text": "x", "scope": "note"})
    assert req.consequences is None
    assert req.next_steps is None
    assert req.options is None
