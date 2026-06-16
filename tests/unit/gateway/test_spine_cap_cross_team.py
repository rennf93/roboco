"""Spine-cap concurrency rules for delegated subtasks.

The per-parent cap is type-aware (`_SPINE_TYPE_CAPS`):
  - ``code``: no per-parent cap. A PM delegates a full per-dev queue up front
    (per-dev sequenced queues); both cell devs build in parallel and each works
    its own queue one task at a time (enforced by the orchestrator's per-lane
    dispatch barrier, not this cap). Total fan-out is bounded by the 12-subtask
    hard cap. Only an exact same-title duplicate to one dev is rejected here.
  - ``planning`` / ``documentation``: 1.

Cross-team exemption:
  - ``planning`` on a different team does NOT count toward the cap — that's
    main_pm's legitimate cross-cell fanout (be-pm + fe-pm + ux-pm in parallel).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from roboco.services.gateway.choreographer._impl import Choreographer


def _sibling(
    *,
    task_type: str,
    team: str,
    status: str = "pending",
    assignee: str = "some-pm",
    title: str = "some unit",
) -> MagicMock:
    sib = MagicMock()
    sib.id = "11111111-aaaa-bbbb-cccc-dddddddddddd"
    sib.status = status
    sib.task_type = task_type
    sib.team = team
    sib.assigned_to = assignee
    sib.title = title
    return sib


def test_cross_team_planning_fanout_is_allowed() -> None:
    """main-pm: planning→be-pm (backend) exists; planning→fe-pm (frontend) must pass."""
    sib = _sibling(task_type="planning", team="backend", assignee="be-pm")
    env = Choreographer._sibling_cap_envelope(
        siblings=[sib],
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
    sib = _sibling(task_type="planning", team="backend", assignee="be-pm")
    env = Choreographer._sibling_cap_envelope(
        siblings=[sib],
        new_type="planning",
        new_team="ux_ui",
        new_assignee="ux-pm",
    )
    assert env is None, env


def test_same_team_planning_still_rejected() -> None:
    """Two planning subtasks on the SAME team is over-decomposition —
    must still be blocked by the spine-cap (planning cap is 1)."""
    sib = _sibling(task_type="planning", team="backend", assignee="be-pm")
    env = Choreographer._sibling_cap_envelope(
        siblings=[sib],
        new_type="planning",
        new_team="backend",
        new_assignee="be-pm-2",
    )
    assert env is not None
    body = env.as_dict()
    assert body["error"] == "invalid_state", body


def test_one_code_sibling_allows_a_second_parallel_dev() -> None:
    """Code cap is 2 — one in flight to be-dev-1 must NOT block a second to
    be-dev-2. This is the two-devs-per-cell parallelism the cap exists to allow."""
    sib = _sibling(task_type="code", team="backend", assignee="be-dev-1")
    env = Choreographer._sibling_cap_envelope(
        siblings=[sib],
        new_type="code",
        new_team="backend",
        new_assignee="be-dev-2",
    )
    assert env is None, f"A second parallel code subtask must be allowed. Got: {env}"


def test_third_code_subtask_is_allowed_queue() -> None:
    """No per-parent code cap: a third (and beyond) code subtask is allowed so a
    PM can delegate a full per-dev queue up front. Concurrency is bounded by the
    per-lane dispatch barrier, not by rejecting the delegate."""
    sibs = [
        _sibling(task_type="code", team="backend", assignee="be-dev-1", title="u1"),
        _sibling(task_type="code", team="backend", assignee="be-dev-2", title="u2"),
    ]
    env = Choreographer._sibling_cap_envelope(
        siblings=sibs,
        new_type="code",
        new_team="backend",
        new_assignee="be-dev-1",
        new_title="u3",
    )
    assert env is None, f"A third code subtask (queue) must be allowed. Got: {env}"


def test_second_distinct_code_to_same_dev_is_allowed_queue() -> None:
    """A dev legitimately owns a QUEUE — a second code subtask to the same dev
    with a DISTINCT title is allowed (it's the dev's next queue item)."""
    sib = _sibling(
        task_type="code", team="backend", assignee="be-dev-1", title="add login form"
    )
    env = Choreographer._sibling_cap_envelope(
        siblings=[sib],
        new_type="code",
        new_team="backend",
        new_assignee="be-dev-1",
        new_title="add logout button",
    )
    assert env is None, f"A distinct queue item for the same dev must pass. Got: {env}"


def test_exact_duplicate_code_to_same_dev_is_rejected() -> None:
    """An exact same-title code subtask to a dev that already owns one is an
    accidental re-delegation — rejected even though queues are allowed.
    Title match is case/whitespace-insensitive."""
    sib = _sibling(
        task_type="code", team="backend", assignee="be-dev-1", title="Add Login Form"
    )
    env = Choreographer._sibling_cap_envelope(
        siblings=[sib],
        new_type="code",
        new_team="backend",
        new_assignee="be-dev-1",
        new_title="add   login form",
    )
    assert env is not None
    assert env.as_dict()["error"] == "invalid_state", env


def test_documentation_still_capped_at_one() -> None:
    """Documentation subtasks stay capped at 1 regardless of team."""
    sib = _sibling(task_type="documentation", team="backend", assignee="be-doc")
    env = Choreographer._sibling_cap_envelope(
        siblings=[sib],
        new_type="documentation",
        new_team="frontend",
        new_assignee="fe-doc",
    )
    assert env is not None


def test_planning_with_missing_team_still_rejected() -> None:
    """If either side has no team (defensive), fall back to the strict cap —
    don't let an empty-team escape hatch sneak a second planning past."""
    sib_no_team = _sibling(task_type="planning", team="", assignee="be-pm")
    env = Choreographer._sibling_cap_envelope(
        siblings=[sib_no_team],
        new_type="planning",
        new_team="backend",
        new_assignee="be-pm-2",
    )
    assert env is not None, "Empty team on sibling must NOT bypass the cap"

    sib = _sibling(task_type="planning", team="backend", assignee="be-pm")
    env2 = Choreographer._sibling_cap_envelope(
        siblings=[sib],
        new_type="planning",
        new_team="",
        new_assignee="be-pm-2",
    )
    assert env2 is not None, "Empty team on new task must NOT bypass the cap"
