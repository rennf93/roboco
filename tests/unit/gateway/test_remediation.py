"""Tests for remediation hint generation."""

from __future__ import annotations

from roboco.services.gateway.remediation import (
    hint_for_missing_plan,
    hint_for_missing_progress,
    hint_for_missing_reflect,
    hint_for_unaddressed_acceptance_criteria,
    hint_for_unread_a2a,
)


def test_missing_plan_hint() -> None:
    h = hint_for_missing_plan(task_id="abc-123")
    assert "i_will_work_on" in h
    assert "abc-123" in h
    assert "plan=" in h


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


def test_unread_a2a_hint() -> None:
    h = hint_for_unread_a2a(count=2, task_id="t-1")
    assert "2" in h
    assert "t-1" in h
