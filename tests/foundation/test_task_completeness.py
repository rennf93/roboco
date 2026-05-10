"""Tier 1 — task-completeness rules."""

from __future__ import annotations

from types import SimpleNamespace

from roboco.foundation.policy import task_completeness as tc

MIN_HINT_LEN = 20
PARENT_PRIORITY_HIGH = 4  # parent task priority used for inheritance assertions
DEFAULT_PRIORITY_MEDIUM = 2  # fill_priority_from_parent default when no parent


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


def test_fill_team_from_assignee_resolves_dev_slug() -> None:
    payload = {"assigned_to": "be-dev-1"}
    result = tc.fill_team_from_assignee(payload)
    assert result["team"] == "backend"
    assert result["assigned_to"] == "be-dev-1"


def test_fill_team_from_assignee_does_not_overwrite_explicit_team() -> None:
    payload = {"assigned_to": "be-dev-1", "team": "frontend"}
    result = tc.fill_team_from_assignee(payload)
    # Auto-fill never silently overwrites; team stays as caller passed it.
    assert result["team"] == "frontend"


def test_fill_team_from_assignee_unknown_slug_returns_unchanged() -> None:
    payload = {"assigned_to": "notreal-1"}
    result = tc.fill_team_from_assignee(payload)
    # Auto-fill is best-effort; unknown slug = no fill, downstream rejects.
    assert "team" not in result


def test_fill_priority_from_parent_inherits() -> None:
    payload = {}
    parent = SimpleNamespace(priority=PARENT_PRIORITY_HIGH)
    result = tc.fill_priority_from_parent(payload, parent)
    assert result["priority"] == PARENT_PRIORITY_HIGH
    assert result["__priority_inherited"] is True


def test_fill_priority_from_parent_does_not_overwrite_explicit() -> None:
    payload = {"priority": 1}
    parent = SimpleNamespace(priority=PARENT_PRIORITY_HIGH)
    result = tc.fill_priority_from_parent(payload, parent)
    assert result["priority"] == 1
    assert "__priority_inherited" not in result


def test_fill_priority_from_parent_no_parent_uses_medium_default() -> None:
    payload = {}
    result = tc.fill_priority_from_parent(payload, None)
    assert result["priority"] == DEFAULT_PRIORITY_MEDIUM
    assert result["__priority_inherited"] is True


def test_fill_parent_from_active_task_sets_id() -> None:
    payload = {}
    result = tc.fill_parent_from_active_task(payload, "task-id-123")
    assert result["parent_task_id"] == "task-id-123"


def test_fill_parent_from_active_task_does_not_overwrite_explicit() -> None:
    payload = {"parent_task_id": "explicit-id"}
    result = tc.fill_parent_from_active_task(payload, "active-id")
    assert result["parent_task_id"] == "explicit-id"
