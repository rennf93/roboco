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
    with pytest.raises(GitRequirementError, match="docs_complete"):
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
