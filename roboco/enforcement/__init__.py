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
    VALID_TRANSITIONS,
    TaskLifecycleError,
    validate_task_transition,
)
from roboco.enforcement.task_ownership import (
    TaskOwnershipError,
    validate_task_claim,
    validate_task_ownership,
)

__all__ = [
    "CHANNEL_ACCESS",
    "VALID_TRANSITIONS",
    "ChannelAccessDeniedError",
    "NotificationPermissionError",
    "TaskLifecycleError",
    "TaskOwnershipError",
    "validate_channel_access",
    "validate_notification_permission",
    "validate_task_claim",
    "validate_task_ownership",
    "validate_task_transition",
]
