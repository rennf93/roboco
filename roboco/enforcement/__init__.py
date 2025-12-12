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
    validate_channel_access,
)
from roboco.enforcement.notification_perms import (
    NotificationPermissionError,
    validate_notification_permission,
)
from roboco.enforcement.task_lifecycle import (
    ROLE_RESTRICTED_TRANSITIONS,
    VALID_TRANSITIONS,
    TaskLifecycleError,
    can_agent_transition,
    validate_task_transition,
)
from roboco.enforcement.task_ownership import (
    TaskClaimContext,
    TaskOwnershipError,
    validate_task_claim,
    validate_task_ownership,
)

__all__ = [
    "CHANNEL_ACCESS",
    "ROLE_RESTRICTED_TRANSITIONS",
    "VALID_TRANSITIONS",
    "ChannelAccessDeniedError",
    "NotificationPermissionError",
    "TaskClaimContext",
    "TaskLifecycleError",
    "TaskOwnershipError",
    "can_agent_transition",
    "validate_channel_access",
    "validate_notification_permission",
    "validate_task_claim",
    "validate_task_ownership",
    "validate_task_transition",
]
