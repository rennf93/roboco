"""Coverage for roboco.enforcement.task_ownership."""

from __future__ import annotations

import pytest
from roboco.enforcement.task_ownership import (
    TaskOwnershipError,
    can_review_task,
    validate_task_ownership,
)


def test_reassign_by_main_pm_allowed() -> None:
    assert (
        validate_task_ownership(
            agent_id="main-pm",
            task_id="t1",
            task_assigned_to=None,
            task_team="backend",
            action="reassign",
        )
        is True
    )


def test_reassign_by_cell_pm_in_their_cell_allowed() -> None:
    assert (
        validate_task_ownership(
            agent_id="be-pm",
            task_id="t1",
            task_assigned_to=None,
            task_team="backend",
            action="reassign",
        )
        is True
    )


def test_reassign_by_developer_denied() -> None:
    """Non-PM cannot reassign (lines 69-75)."""
    with pytest.raises(TaskOwnershipError) as exc:
        validate_task_ownership(
            agent_id="be-dev-1",
            task_id="t1",
            task_assigned_to=None,
            task_team="backend",
            action="reassign",
        )
    assert "Only PMs" in exc.value.message


def test_reassign_by_cell_pm_outside_cell_denied() -> None:
    """Cell PM trying to reassign tasks in another cell (lines 77-83)."""
    with pytest.raises(TaskOwnershipError) as exc:
        validate_task_ownership(
            agent_id="be-pm",
            task_id="t1",
            task_assigned_to=None,
            task_team="frontend",
            action="reassign",
        )
    assert "their cell" in exc.value.message


def test_view_action_always_allowed() -> None:
    """View action returns True for any agent (line 88)."""
    assert (
        validate_task_ownership(
            agent_id="be-dev-1",
            task_id="t1",
            task_assigned_to="someone-else",
            task_team="backend",
            action="view",
        )
        is True
    )


def test_other_action_assigned_to_agent_allowed() -> None:
    assert (
        validate_task_ownership(
            agent_id="be-dev-1",
            task_id="t1",
            task_assigned_to="be-dev-1",
            task_team="backend",
            action="update",
        )
        is True
    )


def test_other_action_not_assigned_denied() -> None:
    """Action by non-assignee raises TaskOwnershipError."""
    with pytest.raises(TaskOwnershipError) as exc:
        validate_task_ownership(
            agent_id="be-dev-1",
            task_id="t1",
            task_assigned_to="be-dev-2",
            task_team="backend",
            action="update",
        )
    assert "be-dev-2" in exc.value.message


def test_can_review_task_self_review_denied() -> None:
    """Cannot review your own work (line 118)."""
    assert can_review_task("be-qa", "be-qa") is False


def test_can_review_task_other_agent_allowed() -> None:
    assert can_review_task("be-qa", "be-dev-1") is True


def test_task_ownership_error_default_message() -> None:
    err = TaskOwnershipError(agent_id="a", task_id="t", action="claim")
    assert "claim" in err.message
    assert err.agent_id == "a"
    assert err.task_id == "t"
    assert err.action == "claim"
