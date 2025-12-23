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
"""

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
    TaskLifecycleError,
    can_agent_transition,
    get_valid_transitions,
    is_active_state,
    is_terminal_state,
    is_waiting_state,
    validate_task_transition,
)
from roboco.enforcement.task_ownership import (
    TaskClaimContext,
    TaskOwnershipError,
    can_review_task,
    validate_task_claim,
    validate_task_ownership,
)

__all__ = [
    "CHANNEL_ACCESS",
    "ROLE_RESTRICTED_TRANSITIONS",
    "VALID_TRANSITIONS",
    "ChannelAccessDeniedError",
    "JournalAccessDeniedError",
    "NotificationPermissionError",
    "TaskClaimContext",
    "TaskLifecycleError",
    "TaskOwnershipError",
    "can_agent_transition",
    "can_read_journal",
    "can_review_task",
    "get_agent_channels",
    "get_notification_scope",
    "get_readable_journals",
    "get_valid_transitions",
    "is_active_state",
    "is_terminal_state",
    "is_waiting_state",
    "validate_channel_access",
    "validate_journal_access",
    "validate_notification_permission",
    "validate_task_claim",
    "validate_task_ownership",
    "validate_task_transition",
]
