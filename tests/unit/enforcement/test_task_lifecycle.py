"""enforcement.task_lifecycle coverage."""

from __future__ import annotations

import pytest
from roboco.enforcement.task_lifecycle import (
    GitContext,
    GitRequirementError,
    can_agent_transition,
    check_parallel_completion,
    get_valid_transitions,
    is_active_state,
    is_terminal_state,
    is_waiting_state,
    sla_seconds_for,
    validate_git_requirements,
    validate_task_transition,
)
from roboco.exceptions import TaskLifecycleError
from roboco.foundation.policy.lifecycle import Status

# ---------------------------------------------------------------------------
# validate_task_transition
# ---------------------------------------------------------------------------


def test_valid_transition_pending_to_claimed() -> None:
    assert validate_task_transition("pending", "claimed") is True


def test_invalid_transition_raises() -> None:
    with pytest.raises(TaskLifecycleError):
        validate_task_transition("pending", "completed")


def test_terminal_states_have_no_outgoing() -> None:
    with pytest.raises(TaskLifecycleError):
        validate_task_transition("completed", "claimed")


def test_can_agent_transition_returns_bool() -> None:
    assert can_agent_transition("pending", "claimed", "developer") is True


def test_can_agent_transition_invalid_returns_false() -> None:
    assert can_agent_transition("pending", "completed", "developer") is False


# ---------------------------------------------------------------------------
# get_valid_transitions / state predicates
# ---------------------------------------------------------------------------


def test_get_valid_transitions_for_pending() -> None:
    transitions = get_valid_transitions("pending")
    assert "claimed" in transitions
    assert "cancelled" in transitions


def test_get_valid_transitions_for_completed() -> None:
    assert get_valid_transitions("completed") == []


def test_get_valid_transitions_unknown_status() -> None:
    assert get_valid_transitions("ghost") == []


def test_is_terminal_state_for_completed() -> None:
    assert is_terminal_state("completed") is True


def test_is_terminal_state_for_cancelled() -> None:
    assert is_terminal_state("cancelled") is True


def test_is_terminal_state_for_in_progress() -> None:
    assert is_terminal_state("in_progress") is False


def test_is_waiting_state_for_blocked() -> None:
    assert is_waiting_state("blocked") is True


def test_is_waiting_state_for_in_progress() -> None:
    assert is_waiting_state("in_progress") is False


def test_is_active_state_for_claimed() -> None:
    assert is_active_state("claimed") is True


def test_is_active_state_for_completed() -> None:
    assert is_active_state("completed") is False


# ---------------------------------------------------------------------------
# Git requirements
# ---------------------------------------------------------------------------


def test_validate_git_no_context_passes() -> None:
    """Without git_ctx, all transitions pass git checks."""
    assert validate_git_requirements("claimed", "in_progress", None) is True


def test_git_doc_to_pm_review_requires_docs_complete() -> None:
    ctx = GitContext(docs_complete=False, pr_created=True)
    with pytest.raises(GitRequirementError, match="documentation not yet complete"):
        validate_git_requirements("awaiting_documentation", "awaiting_pm_review", ctx)


def test_git_doc_to_pm_review_requires_pr_created() -> None:
    ctx = GitContext(docs_complete=True, pr_created=False)
    with pytest.raises(GitRequirementError, match="PR not yet created"):
        validate_git_requirements("awaiting_documentation", "awaiting_pm_review", ctx)


def test_git_doc_to_pm_review_succeeds_when_both_complete() -> None:
    ctx = GitContext(docs_complete=True, pr_created=True)
    assert (
        validate_git_requirements("awaiting_documentation", "awaiting_pm_review", ctx)
        is True
    )


def test_git_pm_to_ceo_requires_pr_number() -> None:
    ctx = GitContext(pr_number=None)
    with pytest.raises(GitRequirementError, match="pr_number"):
        validate_git_requirements("awaiting_pm_review", "awaiting_ceo_approval", ctx)


def test_git_pm_to_ceo_succeeds_with_pr() -> None:
    ctx = GitContext(pr_number=42)
    assert (
        validate_git_requirements("awaiting_pm_review", "awaiting_ceo_approval", ctx)
        is True
    )


def test_git_claimed_to_in_progress_requires_branch() -> None:
    ctx = GitContext(branch_name=None)
    with pytest.raises(GitRequirementError, match="no branch"):
        validate_git_requirements("claimed", "in_progress", ctx)


def test_git_claimed_to_in_progress_succeeds_with_branch() -> None:
    ctx = GitContext(branch_name="feature/x")
    assert validate_git_requirements("claimed", "in_progress", ctx) is True


def test_git_claimed_to_in_progress_coordination_task_needs_no_branch() -> None:
    # A coordination/fan-out task (product, no repo of its own) does no git and
    # never gets a branch — it must reach in_progress so Main PM can delegate.
    # Without this exemption, start() raised GitRequirementError and the whole
    # board->cells fan-out deadlocked (the PM looped on i_will_plan).
    ctx = GitContext(branch_name=None, is_coordination=True)
    assert validate_git_requirements("claimed", "in_progress", ctx) is True


def test_git_claimed_to_in_progress_still_blocks_branchless_code_task() -> None:
    # A normal code task with no branch is still blocked (regression guard).
    ctx = GitContext(branch_name=None, is_coordination=False)
    with pytest.raises(GitRequirementError, match="no branch"):
        validate_git_requirements("claimed", "in_progress", ctx)


# ---------------------------------------------------------------------------
# check_parallel_completion
# ---------------------------------------------------------------------------


def test_check_parallel_completion_both_done() -> None:
    assert check_parallel_completion(docs_complete=True, pr_created=True) is True


def test_check_parallel_completion_docs_only() -> None:
    assert check_parallel_completion(docs_complete=True, pr_created=False) is False


def test_check_parallel_completion_pr_only() -> None:
    assert check_parallel_completion(docs_complete=False, pr_created=True) is False


def test_check_parallel_completion_neither() -> None:
    assert check_parallel_completion(docs_complete=False, pr_created=False) is False


# ---------------------------------------------------------------------------
# SLA seconds
# ---------------------------------------------------------------------------


def test_sla_seconds_for_developer_in_progress() -> None:
    result = sla_seconds_for("developer", "in_progress")
    assert result is None or isinstance(result, int)


def test_sla_seconds_for_unknown_pair() -> None:
    assert sla_seconds_for("ghost", "unknown_status") is None


def test_sla_seconds_for_no_role() -> None:
    assert sla_seconds_for(None, "in_progress") is None


# ---------------------------------------------------------------------------
# Cancel from the CEO approval queue is the CEO's call, not a PM's
# ---------------------------------------------------------------------------


def test_cancel_from_awaiting_ceo_is_ceo_only() -> None:
    """A PM must not cancel a task the CEO is reviewing — that bypasses the
    human CEO-approval gate. Only the CEO can cancel from awaiting_ceo_approval."""
    assert can_agent_transition("awaiting_ceo_approval", "cancelled", "ceo") is True
    assert (
        can_agent_transition("awaiting_ceo_approval", "cancelled", "cell_pm") is False
    )
    assert (
        can_agent_transition("awaiting_ceo_approval", "cancelled", "main_pm") is False
    )


def test_cancel_from_other_non_terminal_remains_pm_plus_ceo() -> None:
    """The CEO-only narrowing is scoped to the approval queue; elsewhere a PM
    may still cancel (unchanged behavior)."""
    assert can_agent_transition("in_progress", "cancelled", "cell_pm") is True
    assert can_agent_transition("awaiting_qa", "cancelled", "main_pm") is True


def test_validate_cancel_from_awaiting_ceo_raises_for_pm() -> None:
    with pytest.raises(TaskLifecycleError):
        validate_task_transition("awaiting_ceo_approval", "cancelled", "cell_pm")
    # CEO is allowed — no raise.
    assert validate_task_transition("awaiting_ceo_approval", "cancelled", "ceo") is True


# ---------------------------------------------------------------------------
# VERIFYING must go through the QA hop (awaiting_qa), not straight to docs
# ---------------------------------------------------------------------------


def test_verifying_to_awaiting_documentation_is_rejected() -> None:
    """VERIFYING is the dev self-verification state; the canonical exit is
    submit_qa -> awaiting_qa -> (qa_pass) -> awaiting_documentation. A spurious
    verifying->awaiting_documentation edge bypassed the entire QA review hop."""
    with pytest.raises(TaskLifecycleError):
        validate_task_transition("verifying", "awaiting_documentation", "qa")


def test_verifying_self_fail_to_needs_revision_still_allowed() -> None:
    """The legitimate self-fail out of verifying (QA/PM only) is preserved."""
    assert validate_task_transition("verifying", "needs_revision", "qa") is True


# ---------------------------------------------------------------------------
# is_waiting_state must cover the in-path PR-review gate
# ---------------------------------------------------------------------------


def test_is_waiting_state_includes_awaiting_pr_review() -> None:
    """The PR-review gate parks the PM on the reviewer; it is a waiting state,
    not an active one (the predicates were never updated when AWAITING_PR_REVIEW
    was added to the enum)."""
    assert is_waiting_state("awaiting_pr_review") is True
    assert is_active_state("awaiting_pr_review") is False


def test_status_classification_is_mutually_disjoint() -> None:
    """No status may be classified as both active and waiting — the structural
    invariant that catches miscategorization (the awaiting_pr_review leak was
    an instance of a status falling into no category)."""
    active = {s.value for s in Status if is_active_state(s.value)}
    waiting = {s.value for s in Status if is_waiting_state(s.value)}
    terminal = {s.value for s in Status if is_terminal_state(s.value)}
    assert active & waiting == set()
    assert active & terminal == set()
    assert waiting & terminal == set()


def test_status_classification_covers_every_enum_member() -> None:
    """Every Status enum member must be classified by EXACTLY one of
    is_terminal_state / is_active_state / is_waiting_state — the coverage
    invariant that catches a future enum addition silently falling through
    into no category (backlog/pending leaked into none before the fix)."""
    all_statuses = {s.value for s in Status}
    classified = {
        s.value
        for s in Status
        if is_terminal_state(s.value)
        or is_active_state(s.value)
        or is_waiting_state(s.value)
    }
    missing = all_statuses - classified
    assert not missing, f"statuses classified by NO predicate: {sorted(missing)}"
    for s in Status:
        v = s.value
        n = sum(
            [
                is_terminal_state(v),
                is_active_state(v),
                is_waiting_state(v),
            ]
        )
        assert n == 1, f"{v} classified by {n} predicates, expected exactly 1"
