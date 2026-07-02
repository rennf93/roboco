"""Team-match must actually fire: the spec gate rejects cross-team actors.

Live 2026-07-02: a frontend cell PM was dispatched onto a BACKEND task's
review, then blocked it, escalated it, and briefly held it — a 40-minute
ownership tug-of-war. Seventeen ActionSpecs carry needs_team_match=True and
_check_team_match enforces it — but only when the caller supplies
Context.agent_team, which no choreographer site did, so the gate sat in its
permissive fallback forever. These tests pin the policy behavior the
choreographer sweep wires up.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

from roboco.foundation.policy.lifecycle import (
    Context,
    Role,
    can_invoke_intent,
)
from roboco.models.base import TaskStatus


def _task(**overrides: Any) -> Any:
    base: dict[str, Any] = {
        "id": uuid4(),
        "status": TaskStatus.IN_PROGRESS,
        "team": "backend",
        "assigned_to": None,
        "task_type": "code",
    }
    base.update(overrides)
    return cast("Any", SimpleNamespace(**base))


def test_cross_team_developer_is_rejected_when_team_supplied() -> None:
    decision = can_invoke_intent(
        Role.DEVELOPER,
        "i_am_blocked",
        _task(team="backend", status=TaskStatus.IN_PROGRESS, task_type="code"),
        Context(actor_id=uuid4(), agent_team="frontend"),
    )
    assert not decision.allowed
    assert "team" in (decision.message or "").lower()


def test_cross_team_cell_pm_resume_is_rejected() -> None:
    decision = can_invoke_intent(
        Role.CELL_PM,
        "resume",
        _task(team="backend", status=TaskStatus.PAUSED),
        Context(actor_id=uuid4(), agent_team="frontend"),
    )
    assert not decision.allowed
    assert "team" in (decision.message or "").lower()


def test_same_team_developer_is_allowed() -> None:
    decision = can_invoke_intent(
        Role.DEVELOPER,
        "i_am_blocked",
        _task(team="backend", status=TaskStatus.IN_PROGRESS, task_type="code"),
        Context(actor_id=uuid4(), agent_team="backend"),
    )
    assert decision.allowed


def test_missing_team_keeps_permissive_fallback() -> None:
    """Absent agent_team defers to the service layer (backward compatible)."""
    decision = can_invoke_intent(
        Role.DEVELOPER,
        "i_am_blocked",
        _task(team="backend", status=TaskStatus.IN_PROGRESS, task_type="code"),
        Context(actor_id=uuid4()),
    )
    assert decision.allowed


def test_org_wide_roles_are_exempt_cross_team() -> None:
    """Main PM handles every cell's escalations; the exemption keeps that."""
    for role, verb, status in (
        (Role.MAIN_PM, "resume", TaskStatus.PAUSED),
        (Role.MAIN_PM, "unblock", TaskStatus.BLOCKED),
    ):
        decision = can_invoke_intent(
            role,
            verb,
            _task(team="backend", status=status),
            Context(actor_id=uuid4(), agent_team="main_pm"),
        )
        assert decision.allowed, f"{role} {verb} must stay org-wide"


def test_cross_team_cell_pm_unblock_is_rejected() -> None:
    decision = can_invoke_intent(
        Role.CELL_PM,
        "unblock",
        _task(team="backend", status=TaskStatus.BLOCKED),
        Context(actor_id=uuid4(), agent_team="frontend"),
    )
    assert not decision.allowed
    assert "team" in (decision.message or "").lower()
