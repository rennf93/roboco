"""Tests for remediation hint generation."""

from __future__ import annotations

from roboco.services.gateway.remediation import (
    hint_for_missing_ac_coverage,
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


def test_missing_ac_coverage_hint_shows_call_shape_and_real_ids() -> None:
    h = hint_for_missing_ac_coverage(
        criteria=[("id-a", "Criterion A"), ("id-b", "Criterion B")],
        title="Orphan slice",
    )
    assert "delegate(title='Orphan slice'" in h
    assert "covers_parent_criteria=['id-a']" in h
    assert 'id-a="Criterion A"' in h
    assert 'id-b="Criterion B"' in h


def test_missing_ac_coverage_hint_handles_no_criteria() -> None:
    h = hint_for_missing_ac_coverage(criteria=[], title="X")
    assert "<id>" in h
