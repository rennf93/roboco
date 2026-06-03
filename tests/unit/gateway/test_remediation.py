"""Tests for remediation hint generation."""

from __future__ import annotations

from roboco.services.gateway.remediation import (
    hint_for_missing_progress,
    hint_for_missing_reflect,
    hint_for_unaddressed_acceptance_criteria,
)


def test_missing_progress_hint() -> None:
    h = hint_for_missing_progress()
    assert "commit" in h.lower() or "progress" in h.lower()


def test_missing_reflect_hint() -> None:
    h = hint_for_missing_reflect(task_id="xyz-789")
    assert "note(scope='reflect'" in h
    assert "xyz-789" in h


def test_unaddressed_criteria_hint() -> None:
    h = hint_for_unaddressed_acceptance_criteria(
        criteria=["criterion 1", "criterion 3"], task_id="t-1"
    )
    assert "criterion 1" in h
    assert "criterion 3" in h
    assert "t-1" in h
