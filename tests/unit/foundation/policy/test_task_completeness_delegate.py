"""TASK_AT_DELEGATE — code delegations must declare a collision surface.

Live break (2026-07-02, f3e1afc5): a PM delegated two code subtasks to two
devs with NO intends_to_touch; the sibling collision analyzer treats a
no-surface sibling as parallel to everything, so both dispatched at once and
seq#1 started before seq#0 against a base missing its prerequisite. The
surface is what turns sibling ordering into real dependency edges — an empty
one silently disables sequencing.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from roboco.foundation.policy.task_completeness import (
    TASK_AT_CREATE,
    TASK_AT_DELEGATE,
    check,
)
from roboco.models.base import TaskType


def _payload(**overrides: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "title": "Implement endpoint",
        "description": "Add /v1/foo endpoint with passing tests please",
        "assigned_to": "be-dev-1",
        "team": "backend",
        "task_type": "code",
        "nature": "technical",
        "estimated_complexity": "medium",
        "acceptance_criteria": ["GET /v1/foo returns 200 with body"],
        "intends_to_touch": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_code_delegation_without_surface_is_incomplete() -> None:
    result = check(TASK_AT_DELEGATE, _payload())
    assert not result.passed
    assert "intends_to_touch" in result.missing
    assert "intends_to_touch" in result.field_hints


def test_code_delegation_with_empty_surface_is_incomplete() -> None:
    result = check(TASK_AT_DELEGATE, _payload(intends_to_touch=[]))
    assert not result.passed
    assert "intends_to_touch" in result.missing


def test_code_delegation_with_surface_passes() -> None:
    result = check(
        TASK_AT_DELEGATE,
        _payload(intends_to_touch=["backend/api/routers/foo.py"]),
    )
    assert result.passed


def test_non_code_delegation_needs_no_surface() -> None:
    for task_type in ("research", "documentation", "design", "planning"):
        result = check(TASK_AT_DELEGATE, _payload(task_type=task_type))
        assert result.passed, f"{task_type} must not require a surface"


def test_enum_task_type_is_normalized() -> None:
    result = check(TASK_AT_DELEGATE, _payload(task_type=TaskType.CODE))
    assert not result.passed
    assert "intends_to_touch" in result.missing


def test_task_at_create_is_unchanged() -> None:
    # REST/manual creation keeps the old contract — no surface requirement.
    result = check(TASK_AT_CREATE, _payload())
    assert result.passed
