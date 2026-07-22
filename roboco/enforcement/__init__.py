"""
Enforcement Layer for RoboCo

Provides rule enforcement for all RoboCo operations:
- Notification permissions
- Task lifecycle state machine
- Task ownership rules
- Handoff requirements

Available Utilities (may not all be in use yet):
- Transition helpers: get_valid_transitions, can_agent_transition
- State checks: is_terminal_state, is_active_state, is_waiting_state
- QA utilities: can_review_task

All functions are designed to be imported as needed. Some are internal
helpers used by the primary validate_* functions.
"""

from roboco.enforcement.journal_perms import (
    JournalAccessDeniedError,
    can_read_journal,
    get_readable_journals,
    validate_journal_access,
)
from roboco.enforcement.task_lifecycle import (
    ROLE_RESTRICTED_TRANSITIONS,
    VALID_TRANSITIONS,
    GitContext,
    GitRequirementError,
    TaskLifecycleError,
    can_agent_transition,
    get_valid_transitions,
    is_active_state,
    is_terminal_state,
    is_waiting_state,
    validate_git_requirements,
    validate_task_transition,
)
from roboco.enforcement.task_ownership import (
    TaskOwnershipError,
    can_review_task,
    validate_task_ownership,
)

__all__ = [
    "ROLE_RESTRICTED_TRANSITIONS",
    "VALID_TRANSITIONS",
    "GitContext",
    "GitRequirementError",
    "JournalAccessDeniedError",
    "TaskLifecycleError",
    "TaskOwnershipError",
    "can_agent_transition",
    "can_read_journal",
    "can_review_task",
    "get_readable_journals",
    "get_valid_transitions",
    "is_active_state",
    "is_terminal_state",
    "is_waiting_state",
    "validate_git_requirements",
    "validate_journal_access",
    "validate_task_ownership",
    "validate_task_transition",
]
