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
    "pending": ["claimed"],
    # Claimed - can start or unclaim
    "claimed": ["in_progress", "pending"],
    # In progress - can block, pause, or submit for verification
    "in_progress": ["blocked", "paused", "verifying"],
    # Blocked - can only unblock back to in_progress
    "blocked": ["in_progress"],
    # Paused - can only resume back to in_progress
    "paused": ["in_progress"],
    # Verifying - self verification, can go to QA or back for revision
    "verifying": ["awaiting_qa", "needs_revision", "awaiting_documentation"],
    # Needs revision - back to work
    "needs_revision": ["in_progress"],
    # Awaiting QA - can pass or fail
    "awaiting_qa": ["awaiting_documentation", "needs_revision"],
    # Awaiting documentation - can complete
    "awaiting_documentation": ["completed"],
    # Terminal states
    "completed": [],
    "cancelled": [],
    # Special state for quarantined tasks
    "quarantined": ["pending"],  # Can be un-quarantined back to pending
}


def validate_task_transition(
    current_status: str,
    target_status: str,
) -> bool:
    """
    Validate task state transition is allowed.

    Args:
        current_status: Current task status
        target_status: Target task status

    Returns:
        True if transition is valid

    Raises:
        TaskLifecycleError: If transition is invalid
    """
    valid = VALID_TRANSITIONS.get(current_status, [])

    if target_status not in valid:
        raise TaskLifecycleError(
            current_status=current_status,
            target_status=target_status,
        )

    return True


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
