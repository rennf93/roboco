"""Tier 1 — task-completeness rules."""

from __future__ import annotations

from types import SimpleNamespace

from roboco.foundation.policy import task_completeness as tc

MIN_HINT_LEN = 20


def _task(**fields):
    """Build a SimpleNamespace mimicking a Task with the given fields."""
    defaults = {
        "title": "ok",
        "description": "x" * 30,
        "acceptance_criteria": ["criterion one"],
        "task_type": "code",
        "nature": "technical",
        "estimated_complexity": "medium",
        "team": "backend",
    }
    defaults.update(fields)
    return SimpleNamespace(**defaults)


def test_task_at_create_passes_for_complete_task() -> None:
    result = tc.check(tc.TASK_AT_CREATE, _task())
    assert result.passed is True
    assert result.missing == []


def test_task_at_create_rejects_empty_acceptance_criteria() -> None:
    result = tc.check(tc.TASK_AT_CREATE, _task(acceptance_criteria=[]))
    assert result.passed is False
    assert "acceptance_criteria" in result.missing


def test_task_at_create_rejects_short_description() -> None:
    result = tc.check(tc.TASK_AT_CREATE, _task(description="too short"))
    assert result.passed is False
    assert "description" in result.missing


def test_task_at_create_rejects_missing_nature() -> None:
    result = tc.check(tc.TASK_AT_CREATE, _task(nature=None))
    assert result.passed is False
    assert "nature" in result.missing


def test_denylist_rejects_silent_fallback_phrase() -> None:
    """Specifically catch the deleted task.py:5061 placeholder."""
    bad = _task(acceptance_criteria=["completed and reviewed by assignee"])
    result = tc.check(tc.TASK_AT_CREATE, bad)
    assert result.passed is False
    assert "acceptance_criteria" in result.missing
    assert any("placeholder" in h.lower() for h in result.field_hints.values())


def test_denylist_rejects_see_title_description() -> None:
    result = tc.check(tc.TASK_AT_CREATE, _task(description="see title"))
    assert result.passed is False
    assert "description" in result.missing


def test_denylist_rejects_todo_description() -> None:
    result = tc.check(tc.TASK_AT_CREATE, _task(description="TODO"))
    assert result.passed is False


def test_completeness_error_carries_missing_and_hints() -> None:
    err = tc.TaskCompletenessError(
        missing=["acceptance_criteria"],
        field_hints={"acceptance_criteria": "non-empty list"},
    )
    assert err.missing == ["acceptance_criteria"]
    assert err.field_hints["acceptance_criteria"] == "non-empty list"


def test_field_hints_for_missing_fields_are_actionable() -> None:
    """Each hint must mention the field and what valid input looks like."""
    result = tc.check(
        tc.TASK_AT_CREATE,
        _task(
            description="",
            acceptance_criteria=[],
            nature=None,
        ),
    )
    for field in ("description", "acceptance_criteria", "nature"):
        assert field in result.field_hints, f"no hint for {field}"
        hint = result.field_hints[field]
        assert len(hint) >= MIN_HINT_LEN, (
            f"hint for {field} too short to be useful: {hint!r}"
        )
