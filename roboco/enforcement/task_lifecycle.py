"""
Task Lifecycle State Machine Enforcement

Validates task state transitions follow the defined lifecycle.

Git Integration:
    Tasks with requires_git=True have additional requirements:
        - awaiting_documentation → awaiting_pm_review:
            requires BOTH docs_complete AND pr_created
        - awaiting_pm_review → awaiting_ceo_approval:
            PR should exist (pr_number set)
        - awaiting_ceo_approval → completed: PR should be merged (CEO merges)

    See validate_git_requirements() for enforcement.
"""

from dataclasses import dataclass

from roboco.exceptions import TaskLifecycleError

# Re-export from exceptions for backward compatibility
__all__ = [
    "VALID_TRANSITIONS",
    "GitContext",
    "GitRequirementError",
    "TaskLifecycleError",
    "check_parallel_completion",
    "validate_git_requirements",
    "validate_task_transition",
]


# =============================================================================
# VALID STATE TRANSITIONS
# =============================================================================

VALID_TRANSITIONS: dict[str, list[str]] = {
    # PM setup phase - task with dependencies or needs session setup
    "backlog": ["pending", "cancelled"],
    # Ready for work state
    "pending": ["claimed", "cancelled"],
    # Claimed - can start, unclaim, or cancel
    "claimed": ["in_progress", "pending", "cancelled"],
    # In progress - can block, pause, verify, submit for PM review, complete, or cancel
    # QA direct assignment: QA can also pass/fail when assigned directly
    "in_progress": [
        "blocked",
        "paused",
        "verifying",
        "awaiting_pm_review",
        "awaiting_documentation",  # QA pass when assigned directly
        "needs_revision",  # QA fail when assigned directly
        "completed",
        "cancelled",
    ],
    # Blocked - can unblock back to in_progress or cancel
    "blocked": ["in_progress", "cancelled"],
    # Paused - can resume back to in_progress or cancel
    "paused": ["in_progress", "cancelled"],
    # Verifying - self verification, can go to QA, revision, or skip to docs
    "verifying": [
        "awaiting_qa",
        "needs_revision",
        "awaiting_documentation",
        "cancelled",
    ],
    # Needs revision - developer claims, works, or PM cancels
    "needs_revision": ["claimed", "in_progress", "cancelled"],
    # Awaiting QA - QA claims, passes, fails, or blocks
    "awaiting_qa": [
        "claimed",
        "awaiting_documentation",
        "needs_revision",
        "blocked",
        "cancelled",
    ],
    # Awaiting documentation - documenter claims or marks done
    "awaiting_documentation": ["claimed", "awaiting_pm_review", "cancelled"],
    # Awaiting PM review - PM claims, then escalates to CEO or completes directly
    "awaiting_pm_review": [
        "claimed",
        "awaiting_ceo_approval",  # Escalate to CEO for final approval
        "completed",  # PM can complete non-escalated tasks
        "cancelled",
    ],
    # Awaiting CEO approval - CEO makes final decision on major tasks
    "awaiting_ceo_approval": [
        "completed",  # CEO approves and merges
        "needs_revision",  # CEO requests changes
        "cancelled",  # CEO cancels
    ],
    # Terminal states - cannot transition out
    "completed": [],
    "cancelled": [],
    # Special state for quarantined tasks
    "quarantined": ["pending"],  # Can be un-quarantined back to pending
}

# =============================================================================
# ROLE-BASED TRANSITION RESTRICTIONS
# =============================================================================

# Roles that can cancel tasks
_CANCEL_ROLES = ["cell_pm", "main_pm", "product_owner", "head_marketing"]

# CEO is the only role that can approve final merges
_CEO_ROLE = ["ceo"]

# Transitions that require specific roles
ROLE_RESTRICTED_TRANSITIONS: dict[tuple[str, str], list[str]] = {
    # Only PM can activate tasks from backlog
    ("backlog", "pending"): _CANCEL_ROLES,
    # Only QA can claim and perform QA actions
    ("awaiting_qa", "claimed"): ["qa"],
    ("awaiting_qa", "awaiting_documentation"): ["qa"],
    ("awaiting_qa", "needs_revision"): ["qa"],
    # QA direct assignment: QA can pass/fail from in_progress when directly assigned
    ("in_progress", "awaiting_documentation"): ["qa"],  # QA pass
    ("in_progress", "needs_revision"): ["qa"],  # QA fail
    # Only documenter can claim docs tasks and mark complete
    ("awaiting_documentation", "claimed"): ["documenter"],
    ("awaiting_documentation", "awaiting_pm_review"): ["documenter"],
    # Only PM can claim PM review tasks
    ("awaiting_pm_review", "claimed"): _CANCEL_ROLES,
    # Only PM can complete tasks (either after PM review or their own work)
    ("awaiting_pm_review", "completed"): _CANCEL_ROLES,
    ("in_progress", "completed"): _CANCEL_ROLES,  # PM completing their own task
    # PM, QA, Documenter can submit for PM review (not developers)
    ("in_progress", "awaiting_pm_review"): [*_CANCEL_ROLES, "qa", "documenter"],
    # Only PM can escalate to CEO approval
    ("awaiting_pm_review", "awaiting_ceo_approval"): _CANCEL_ROLES,
    # CEO approval transitions - only CEO can act
    ("awaiting_ceo_approval", "completed"): _CEO_ROLE,  # CEO approves and merges
    ("awaiting_ceo_approval", "needs_revision"): _CEO_ROLE,  # CEO requests changes
    ("awaiting_ceo_approval", "cancelled"): _CEO_ROLE,  # CEO cancels
    # Only PM or higher can cancel tasks (all states that allow cancel)
    ("backlog", "cancelled"): _CANCEL_ROLES,
    ("pending", "cancelled"): _CANCEL_ROLES,
    ("claimed", "cancelled"): _CANCEL_ROLES,
    ("in_progress", "cancelled"): _CANCEL_ROLES,
    ("blocked", "cancelled"): _CANCEL_ROLES,
    ("paused", "cancelled"): _CANCEL_ROLES,
    ("verifying", "cancelled"): _CANCEL_ROLES,
    ("needs_revision", "cancelled"): _CANCEL_ROLES,
    ("awaiting_qa", "cancelled"): _CANCEL_ROLES,
    ("awaiting_documentation", "cancelled"): _CANCEL_ROLES,
    ("awaiting_pm_review", "cancelled"): _CANCEL_ROLES,
}


def validate_task_transition(
    current_status: str,
    target_status: str,
    agent_role: str | None = None,
) -> bool:
    """
    Validate task state transition is allowed.

    Args:
        current_status: Current task status
        target_status: Target task status
        agent_role: Optional agent role for role-based restrictions

    Returns:
        True if transition is valid

    Raises:
        TaskLifecycleError: If transition is invalid or role not permitted
    """
    valid = VALID_TRANSITIONS.get(current_status, [])

    if target_status not in valid:
        raise TaskLifecycleError(
            current_status=current_status,
            target_status=target_status,
            valid_transitions=valid,
        )

    # Check role-based restrictions if role provided
    if agent_role:
        transition_key = (current_status, target_status)
        allowed_roles = ROLE_RESTRICTED_TRANSITIONS.get(transition_key)

        if allowed_roles and agent_role not in allowed_roles:
            raise TaskLifecycleError(
                current_status=current_status,
                target_status=target_status,
                message=(
                    f"Role '{agent_role}' cannot perform this transition. "
                    f"Allowed roles: {allowed_roles}"
                ),
            )

    return True


def can_agent_transition(
    current_status: str,
    target_status: str,
    agent_role: str,
) -> bool:
    """
    Check if an agent with given role can perform a transition.

    Non-raising version of validate_task_transition for checking permissions.

    Returns:
        True if transition is allowed for the agent
    """
    try:
        return validate_task_transition(current_status, target_status, agent_role)
    except TaskLifecycleError:
        return False


def get_valid_transitions(current_status: str) -> list[str]:
    """
    Get list of valid transitions from current status.

    Args:
        current_status: Current task status

    Returns:
        List of valid target statuses
    """
    return VALID_TRANSITIONS.get(current_status, [])


def is_terminal_state(status: str) -> bool:
    """Check if a status is a terminal state."""
    return status in ("completed", "cancelled")


def is_waiting_state(status: str) -> bool:
    """Check if a status is a waiting state (agent can work on other tasks)."""
    return status in (
        "blocked",
        "paused",
        "awaiting_qa",
        "awaiting_documentation",
        "awaiting_pm_review",
        "awaiting_ceo_approval",
    )


def is_active_state(status: str) -> bool:
    """Check if a status is an active working state."""
    return status in ("claimed", "in_progress", "verifying", "needs_revision")


# =============================================================================
# GIT INTEGRATION VALIDATION
# =============================================================================


class GitRequirementError(Exception):
    """Raised when git requirements are not met for a transition."""

    def __init__(
        self,
        transition: tuple[str, str],
        requirement: str,
        message: str | None = None,
    ) -> None:
        self.transition = transition
        self.requirement = requirement
        self.message = message or f"Git requirement not met: {requirement}"
        super().__init__(self.message)


@dataclass
class GitContext:
    """Git-related task state for validation."""

    requires_git: bool = False
    docs_complete: bool = False
    pr_created: bool = False
    pr_number: int | None = None
    branch_name: str | None = None


def validate_git_requirements(
    current_status: str,
    target_status: str,
    git_ctx: GitContext | None = None,
) -> bool:
    """
    Validate git-related requirements for task transitions.

    For tasks with requires_git=True, additional requirements apply:

    - awaiting_documentation → awaiting_pm_review:
        Requires BOTH docs_complete=True AND pr_created=True
        (Documenter and Developer work in parallel)

    - awaiting_pm_review → awaiting_ceo_approval:
        Requires pr_number to be set (PR exists)

    - claimed → in_progress (git tasks):
        Should have branch_name set (auto-created on claim)

    Args:
        current_status: Current task status
        target_status: Target task status
        git_ctx: Git context with workflow state (None = no git requirements)

    Returns:
        True if all requirements met

    Raises:
        GitRequirementError: If git requirements not met
    """
    # Non-git tasks or no context have no git requirements
    if git_ctx is None or not git_ctx.requires_git:
        return True

    transition = (current_status, target_status)

    # awaiting_documentation → awaiting_pm_review
    # Requires BOTH docs AND PR to be ready (parallel workflow)
    if transition == ("awaiting_documentation", "awaiting_pm_review"):
        if not git_ctx.docs_complete:
            raise GitRequirementError(
                transition=transition,
                requirement="docs_complete",
                message=(
                    "Blocked: documentation not yet complete. "
                    "In awaiting_documentation, Documenter and Developer work in "
                    "parallel. Wait for Documenter to call roboco_task_docs_complete()."
                ),
            )
        if not git_ctx.pr_created:
            raise GitRequirementError(
                transition=transition,
                requirement="pr_created",
                message=(
                    "Blocked: PR not yet created. "
                    "In awaiting_documentation, Documenter and Developer work in "
                    "parallel. Wait for Developer to call roboco_git_create_pr()."
                ),
            )

    # awaiting_pm_review → awaiting_ceo_approval: PR should exist for review
    is_ceo_escalation = transition == ("awaiting_pm_review", "awaiting_ceo_approval")
    if is_ceo_escalation and git_ctx.pr_number is None:
        raise GitRequirementError(
            transition=transition,
            requirement="pr_number",
            message=(
                "Cannot escalate to CEO: task has no PR number recorded. "
                "This may indicate the task reached PM review without going through "
                "the git workflow. Check task.pr_number field."
            ),
        )

    # claimed → in_progress (for git tasks)
    # Should have a branch ready
    if transition == ("claimed", "in_progress") and not git_ctx.branch_name:
        raise GitRequirementError(
            transition=transition,
            requirement="branch_name",
            message=(
                "Cannot start work: no branch assigned to this task. "
                "Branches are auto-created on claim. If missing, either "
                "re-claim the task or check if parent task needs claiming first."
            ),
        )

    return True


def check_parallel_completion(
    docs_complete: bool,
    pr_created: bool,
    requires_git: bool = True,
) -> bool:
    """
    Check if parallel execution in awaiting_documentation is complete.

    During awaiting_documentation:
    - Documenter works on docs (sets docs_complete=True)
    - Developer creates PR (sets pr_created=True)

    Both must be true to transition to awaiting_pm_review.

    Args:
        docs_complete: Whether documenter finished
        pr_created: Whether developer created PR
        requires_git: Whether task requires git (if False, only docs needed)

    Returns:
        True if ready to transition to awaiting_pm_review
    """
    if not requires_git:
        return docs_complete

    return docs_complete and pr_created
