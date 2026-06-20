"""Tier 1 — in-path PR-review gate spec self-tests. Fast (no DB, no network).

Covers the lifecycle surface of the assembled-PR review gate: the new
awaiting_pr_review status, the submit_for_review / pr_pass / pr_fail actions,
the reviewer verbs, and the structural invariant that a gated task cannot reach
the PM-merge stage without a pr_pass.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from roboco.foundation.policy import lifecycle as spec
from roboco.foundation.policy.lifecycle import Role, Status


def _task(status: str, team: str = "backend", **kw: Any) -> SimpleNamespace:
    return SimpleNamespace(status=status, team=team, **kw)


def test_submit_for_review_enters_the_gate() -> None:
    assert (
        spec.status_after("submit_for_review", Status.IN_PROGRESS)
        == Status.AWAITING_PR_REVIEW
    )


def test_pr_pass_and_pr_fail_targets() -> None:
    assert (
        spec.status_after("pr_pass", Status.AWAITING_PR_REVIEW)
        == Status.AWAITING_PM_REVIEW
    )
    assert (
        spec.status_after("pr_fail", Status.AWAITING_PR_REVIEW) == Status.NEEDS_REVISION
    )


def test_reviewer_can_pass_and_fail_from_the_gate() -> None:
    t = _task("awaiting_pr_review")
    assert spec.can_invoke_intent(Role.PR_REVIEWER, "pr_pass", t).allowed
    assert spec.can_invoke_intent(Role.PR_REVIEWER, "pr_fail", t).allowed


def test_non_reviewer_roles_cannot_pass_the_gate() -> None:
    t = _task("awaiting_pr_review")
    for role in (Role.DEVELOPER, Role.QA, Role.CELL_PM, Role.MAIN_PM):
        d = spec.can_invoke_intent(role, "pr_pass", t)
        assert not d.allowed
        assert d.rejection_kind == "not_authorized"


def test_reviewer_cannot_pass_from_a_non_gate_state() -> None:
    d = spec.can_invoke_intent(Role.PR_REVIEWER, "pr_pass", _task("awaiting_pm_review"))
    assert not d.allowed
    assert d.rejection_kind == "invalid_state"


def test_reviewer_claims_the_gate_without_transition() -> None:
    assert spec.can_invoke_intent(
        Role.PR_REVIEWER, "claim_gate_review", _task("awaiting_pr_review")
    ).allowed


def test_reviewer_cannot_claim_gate_from_another_roles_state() -> None:
    d = spec.can_invoke_intent(
        Role.PR_REVIEWER, "claim_gate_review", _task("awaiting_qa")
    )
    assert not d.allowed


def test_submit_root_is_main_pm_only_and_opens_a_pr() -> None:
    iv = spec._INTENT_VERBS["submit_root"]
    assert iv.allowed_roles == frozenset({Role.MAIN_PM})
    assert iv.composes == ("submit_for_review",)
    assert iv.pre_side_effects == ("create_root_pr",)


def test_gate_cannot_skip_straight_to_terminal_or_ceo() -> None:
    targets = spec.STATUS_GRAPH[Status.AWAITING_PR_REVIEW]
    assert Status.COMPLETED not in targets
    assert Status.AWAITING_CEO_APPROVAL not in targets
    # The only forward exits are pr_pass (to PM review) and pr_fail (back).
    assert Status.AWAITING_PM_REVIEW in targets
    assert Status.NEEDS_REVISION in targets


def test_pr_pass_is_the_sole_action_out_of_the_gate_into_pm_review() -> None:
    movers = [
        name
        for name, a in spec._ATOMIC_ACTIONS.items()
        if a.target_status == Status.AWAITING_PM_REVIEW
        and Status.AWAITING_PR_REVIEW in a.source_statuses
    ]
    assert movers == ["pr_pass"]


def test_gate_actions_block_self_review() -> None:
    assert spec._ATOMIC_ACTIONS["pr_pass"].self_review_block is True
    assert spec._ATOMIC_ACTIONS["pr_fail"].self_review_block is True


def test_delegate_hint_names_the_role_correct_bubble_up_verb() -> None:
    """After delegating, a PM is steered to the right bubble-up verb proactively:
    a root (no parent) → submit_root; a cell parent → submit_up. Prevents the
    guess-the-verb flail that deadlocked root closure."""
    hint = spec._INTENT_VERBS["delegate"].next_hint
    root = _task("in_progress", parent_task_id=None)
    cell_parent = _task("in_progress", parent_task_id="parent-id")
    assert "submit_root" in hint(root)
    assert "submit_up" in hint(cell_parent)
