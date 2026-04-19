"""
Enforcement Layer for RoboCo

Provides rule enforcement for all RoboCo operations:
- Channel access control
- Notification permissions
- Task lifecycle state machine
- Task ownership rules
- Message validation
- Session boundaries
- Handoff requirements

Available Utilities (may not all be in use yet):
- Transition helpers: get_valid_transitions, can_agent_transition
- State checks: is_terminal_state, is_active_state, is_waiting_state
- Channel utilities: get_agent_channels
- QA utilities: can_review_task

All functions are designed to be imported as needed. Some are internal
helpers used by the primary validate_* functions.
"""

from roboco.enforcement.a2a_access import (
    A2AAccessDeniedError,
    get_a2a_allowed_targets,
    validate_a2a_access,
)
from roboco.enforcement.channel_access import (
    CHANNEL_ACCESS,
    ChannelAccessDeniedError,
    get_agent_channels,
    validate_channel_access,
)
from roboco.enforcement.journal_perms import (
    JournalAccessDeniedError,
    can_read_journal,
    get_readable_journals,
    validate_journal_access,
)
from roboco.enforcement.notification_perms import (
    NotificationPermissionError,
    get_notification_scope,
    validate_notification_permission,
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
    "CHANNEL_ACCESS",
    "ROLE_RESTRICTED_TRANSITIONS",
    "VALID_TRANSITIONS",
    "A2AAccessDeniedError",
    "ChannelAccessDeniedError",
    "GitContext",
    "GitRequirementError",
    "JournalAccessDeniedError",
    "NotificationPermissionError",
    "TaskLifecycleError",
    "TaskOwnershipError",
    "can_agent_transition",
    "can_read_journal",
    "can_review_task",
    "get_a2a_allowed_targets",
    "get_agent_channels",
    "get_notification_scope",
    "get_readable_journals",
    "get_valid_transitions",
    "is_active_state",
    "is_terminal_state",
    "is_waiting_state",
    "validate_a2a_access",
    "validate_channel_access",
    "validate_git_requirements",
    "validate_journal_access",
    "validate_notification_permission",
    "validate_task_ownership",
    "validate_task_transition",
]
