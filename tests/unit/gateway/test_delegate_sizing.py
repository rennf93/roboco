"""Split-before-claim: a code leaf must not bundle too many concerns.

A `code` subtask carrying more acceptance criteria than the hard cap bundles
multiple independent concerns into one leaf — QA can't pass a partial and
criteria get dropped. The gateway rejects it at delegate time so the PM splits
the bundle before any dev can claim it. Moderate bundling is allowed but flagged
in the success envelope (the nudge). `planning` briefs are exempt.
"""

from __future__ import annotations

from roboco.services.gateway.choreographer._impl import Choreographer, DelegateInputs


def _inputs(*, task_type: str, ac_count: int) -> DelegateInputs:
    return DelegateInputs(
        title="t",
        description="d",
        assigned_to="be-dev-1",
        team="backend",
        task_type=task_type,
        nature="technical",
        acceptance_criteria=[f"criterion {i}" for i in range(ac_count)],
    )


def test_small_code_leaf_is_allowed() -> None:
    """A focused code leaf (<= hard cap) passes the sizing guard."""
    assert (
        Choreographer._delegate_sizing_guard(_inputs(task_type="code", ac_count=4))
        is None
    )


def test_code_leaf_at_hard_cap_is_allowed() -> None:
    """Exactly at the hard cap is still allowed; only strictly-above is blocked."""
    cap = Choreographer._SIZING_HARD_AC_COUNT
    assert (
        Choreographer._delegate_sizing_guard(_inputs(task_type="code", ac_count=cap))
        is None
    )


def test_egregiously_bundled_code_leaf_is_rejected() -> None:
    """A code leaf above the hard cap is rejected with split guidance."""
    cap = Choreographer._SIZING_HARD_AC_COUNT
    env = Choreographer._delegate_sizing_guard(
        _inputs(task_type="code", ac_count=cap + 4)
    )
    assert env is not None
    body = env.as_dict()
    assert body["error"] == "invalid_state", body
    assert "Split this into smaller code subtasks" in (env.remediate or "")


def test_planning_brief_is_exempt_from_sizing() -> None:
    """planning subtasks (main_pm -> cell_pm) legitimately carry many criteria."""
    assert (
        Choreographer._delegate_sizing_guard(_inputs(task_type="planning", ac_count=20))
        is None
    )


def test_no_nudge_below_threshold() -> None:
    assert Choreographer._sizing_hint(_inputs(task_type="code", ac_count=5)) is None


def test_nudge_in_moderate_band() -> None:
    """Above the nudge count and below the hard cap: allowed but flagged."""
    hint = Choreographer._sizing_hint(_inputs(task_type="code", ac_count=7))
    assert hint is not None
    assert "7 acceptance criteria" in hint
    assert "parallel" in hint


def test_no_nudge_for_planning() -> None:
    assert (
        Choreographer._sizing_hint(_inputs(task_type="planning", ac_count=20)) is None
    )
