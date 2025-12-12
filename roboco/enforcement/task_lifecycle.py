"""
Task Lifecycle State Machine Enforcement

Validates task state transitions follow the defined lifecycle.
"""

from roboco.exceptions import RobocoError


class TaskLifecycleError(RobocoError):
    """Raised when an invalid task state transition is attempted."""

    def __init__(
        self,
        current_status: str,
        target_status: str,
        message: str | None = None,
    ):
        self.current_status = current_status
        self.target_status = target_status

        valid = VALID_TRANSITIONS.get(current_status, [])
        default_message = (
            f"Cannot transition from '{current_status}' to '{target_status}'. "
            f"Valid transitions: {valid}"
        )

        super().__init__(
            code="INVALID_TASK_TRANSITION",
            message=message or default_message,
            details={
                "current_status": current_status,
                "target_status": target_status,
                "valid_transitions": valid,
            },
        )


# =============================================================================
# VALID STATE TRANSITIONS
# =============================================================================

VALID_TRANSITIONS: dict[str, list[str]] = {
    # Initial state
    "pending": ["claimed", "cancelled"],
    # Claimed - can start, unclaim, or cancel
    "claimed": ["in_progress", "pending", "cancelled"],
    # In progress - can block, pause, submit for verification, or cancel
    "in_progress": ["blocked", "paused", "verifying", "cancelled"],
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
    # Needs revision - back to work or cancel
    "needs_revision": ["in_progress", "cancelled"],
    # Awaiting QA - can pass (to docs) or fail (needs revision) or cancel
    "awaiting_qa": ["awaiting_documentation", "needs_revision", "cancelled"],
    # Awaiting documentation - can complete or cancel
    "awaiting_documentation": ["completed", "cancelled"],
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

# Transitions that require specific roles
ROLE_RESTRICTED_TRANSITIONS: dict[tuple[str, str], list[str]] = {
    # Only QA can pass or fail QA
    ("awaiting_qa", "awaiting_documentation"): ["qa"],
    ("awaiting_qa", "needs_revision"): ["qa"],
    # Only PM or higher can cancel tasks (all states that allow cancel)
    ("pending", "cancelled"): _CANCEL_ROLES,
    ("claimed", "cancelled"): _CANCEL_ROLES,
    ("in_progress", "cancelled"): _CANCEL_ROLES,
    ("blocked", "cancelled"): _CANCEL_ROLES,
    ("paused", "cancelled"): _CANCEL_ROLES,
    ("verifying", "cancelled"): _CANCEL_ROLES,
    ("needs_revision", "cancelled"): _CANCEL_ROLES,
    ("awaiting_qa", "cancelled"): _CANCEL_ROLES,
    ("awaiting_documentation", "cancelled"): _CANCEL_ROLES,
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
    return status in ("blocked", "paused", "awaiting_qa", "awaiting_documentation")


def is_active_state(status: str) -> bool:
    """Check if a status is an active working state."""
    return status in ("claimed", "in_progress", "verifying", "needs_revision")
