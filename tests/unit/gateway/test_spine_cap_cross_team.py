"""Task #157: spine-cap allows cross-team planning fanout.

Pre-fix:
    main_pm delegates a planning subtask to be-pm (backend cell). When
    it then tries to delegate a second planning subtask to fe-pm
    (frontend cell), the spine-cap rejects because there's already a
    non-terminal task_type='planning' under the parent. Pre-gateway
    allowed this parallel cross-cell pattern; the gateway over-applied
    the over-decomposition cap.

Fix:
    `_sibling_dup_envelope` skips the spine-cap when:
      - new task_type == "planning"
      - new team != sibling team (both non-empty)
    Other combinations stay capped:
      - same-team planning: still over-decomposition (real bug)
      - code / documentation regardless of team: a single repo on one
        branch shouldn't have two simultaneous code subtasks
"""

from __future__ import annotations

from unittest.mock import MagicMock

from roboco.services.gateway.choreographer._impl import Choreographer


def _sibling(*, task_type: str, team: str, status: str = "pending") -> MagicMock:
    sib = MagicMock()
    sib.id = "11111111-aaaa-bbbb-cccc-dddddddddddd"
    sib.status = status
    sib.task_type = task_type
    sib.team = team
    sib.assigned_to = "some-pm"
    return sib


def test_cross_team_planning_fanout_is_allowed() -> None:
    """main-pm: planning→be-pm (backend) exists; planning→fe-pm (frontend) must pass."""
    sib = _sibling(task_type="planning", team="backend")
    env = Choreographer._sibling_dup_envelope(
        sibling=sib,
        new_type="planning",
        new_team="frontend",
        new_assignee="fe-pm",
    )
    assert env is None, (
        "Cross-team planning fanout (backend↔frontend) must NOT be rejected. "
        f"Got envelope: {env}"
    )


def test_cross_team_planning_third_cell_also_allowed() -> None:
    """Third cell (ux_ui) is also a valid cross-team planning fanout target."""
    sib = _sibling(task_type="planning", team="backend")
    env = Choreographer._sibling_dup_envelope(
        sibling=sib,
        new_type="planning",
        new_team="ux_ui",
        new_assignee="ux-pm",
    )
    assert env is None, env


def test_same_team_planning_still_rejected() -> None:
    """Two planning subtasks on the SAME team is the real over-decomp pattern —
    must still be blocked by the spine-cap."""
    sib = _sibling(task_type="planning", team="backend")
    env = Choreographer._sibling_dup_envelope(
        sibling=sib,
        new_type="planning",
        new_team="backend",
        new_assignee="be-pm",
    )
    assert env is not None
    body = env.as_dict()
    assert body["error"] == "invalid_state", body


def test_cross_team_code_still_rejected() -> None:
    """Code subtasks stay capped regardless of team — only one code task per
    parent at a time. (Cross-team code under one parent is meaningless;
    each cell's code work lives under its own cell-PM planning task.)"""
    sib = _sibling(task_type="code", team="backend")
    env = Choreographer._sibling_dup_envelope(
        sibling=sib,
        new_type="code",
        new_team="frontend",
        new_assignee="fe-dev-1",
    )
    assert env is not None
    body = env.as_dict()
    assert body["error"] == "invalid_state", body


def test_cross_team_documentation_still_rejected() -> None:
    """Documentation subtasks stay capped regardless of team — single doc
    pass per parent."""
    sib = _sibling(task_type="documentation", team="backend")
    env = Choreographer._sibling_dup_envelope(
        sibling=sib,
        new_type="documentation",
        new_team="frontend",
        new_assignee="fe-doc",
    )
    assert env is not None


def test_planning_with_missing_team_still_rejected() -> None:
    """If either side has no team attribute (defensive), fall back to the
    strict cap. Don't let an empty-team escape hatch sneak past."""
    sib_no_team = _sibling(task_type="planning", team="")
    env = Choreographer._sibling_dup_envelope(
        sibling=sib_no_team,
        new_type="planning",
        new_team="backend",
        new_assignee="be-pm",
    )
    assert env is not None, "Empty team on sibling must NOT bypass the cap"

    sib = _sibling(task_type="planning", team="backend")
    env2 = Choreographer._sibling_dup_envelope(
        sibling=sib,
        new_type="planning",
        new_team="",
        new_assignee="be-pm",
    )
    assert env2 is not None, "Empty team on new task must NOT bypass the cap"
