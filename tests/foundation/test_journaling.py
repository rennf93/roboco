"""Tier 1 — journaling scope catalog."""

from __future__ import annotations

from roboco.foundation.policy import journaling
from roboco.models.base import JournalEntryType


def test_scope_enum_has_five_panel_ui_values() -> None:
    """Panel UI exposes 5 scopes: Notes/Decisions/Reflections/Learnings/Struggles."""
    expected = {"note", "decision", "reflect", "learning", "struggle"}
    actual = {s.value for s in journaling.Scope}
    assert actual == expected, f"Scope drift: {actual ^ expected}"


def test_scope_to_type_covers_every_scope() -> None:
    for scope in journaling.Scope:
        assert scope in journaling.SCOPE_TO_TYPE, f"{scope.value} missing"


def test_scope_to_type_maps_to_canonical_journal_entry_types() -> None:
    assert journaling.SCOPE_TO_TYPE[journaling.Scope.NOTE] == JournalEntryType.GENERAL
    assert (
        journaling.SCOPE_TO_TYPE[journaling.Scope.DECISION]
        == JournalEntryType.DECISION_LOG
    )
    assert (
        journaling.SCOPE_TO_TYPE[journaling.Scope.REFLECT]
        == JournalEntryType.TASK_REFLECTION
    )
    assert (
        journaling.SCOPE_TO_TYPE[journaling.Scope.LEARNING] == JournalEntryType.LEARNING
    )
    assert (
        journaling.SCOPE_TO_TYPE[journaling.Scope.STRUGGLE] == JournalEntryType.STRUGGLE
    )


def test_scope_string_values_match_panel_ui() -> None:
    """The agent-facing string values must match what the UI renders."""
    assert journaling.Scope.NOTE.value == "note"
    assert journaling.Scope.DECISION.value == "decision"
    assert journaling.Scope.REFLECT.value == "reflect"
    assert journaling.Scope.LEARNING.value == "learning"
    assert journaling.Scope.STRUGGLE.value == "struggle"
