"""Smoke-7: spine-cap rejection's remediate forbids the workaround pattern.

Original behavior: cell-PM gets the spine-cap rejection on a second
delegate (parent already has non-terminal task_type='code' subtask).
The model "adapts" by delegating again with task_type='research' or
'documentation' as a "verification" subtask. That works around the
gateway but creates a permanently-stuck orphan subtask that no agent
will ever claim — blocks submit_up forever with
`subtasks not all terminal`.

Fix: remediate explicitly forbids the workaround and tells the agent
to call i_am_idle() instead.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from roboco.services.gateway.choreographer._impl import Choreographer


def test_spine_cap_remediate_forbids_task_type_workaround() -> None:
    """The spine-cap rejection's remediate must say 'do not work around with
    different task_type'."""
    sibling = MagicMock(id="abc12345-...", status="pending")
    env = Choreographer._spine_type_dup_envelope(
        new_type="code",
        sibling=sibling,
        sib_assignee="be-dev-1",
        cap=2,
    )
    remediate = env.remediate or ""
    assert "DO NOT work around" in remediate, (
        f"Spine-cap remediate must forbid the task_type workaround. Got:\n{remediate}"
    )
    assert "different task_type" in remediate
    assert "i_am_idle" in remediate


def test_spine_cap_remediate_warns_about_verification_subtasks() -> None:
    """The remediate must name the specific 'verification subtask' anti-pattern."""
    sibling = MagicMock(id="abc12345-...", status="pending")
    env = Choreographer._spine_type_dup_envelope(
        new_type="code",
        sibling=sibling,
        sib_assignee="be-dev-1",
        cap=2,
    )
    remediate = env.remediate or ""
    # Anti-pattern names — must appear so the model pattern-matches its own behavior.
    assert "verification" in remediate.lower() or "research" in remediate.lower()


def test_spine_cap_envelope_is_invalid_state() -> None:
    """The envelope keeps the invalid_state error kind (so the agent knows
    it's a state issue, not authorization or input shape)."""
    sibling = MagicMock(id="abc12345-...", status="pending")
    env = Choreographer._spine_type_dup_envelope(
        new_type="code",
        sibling=sibling,
        sib_assignee="be-dev-1",
        cap=2,
    )
    assert env.error == "invalid_state"
