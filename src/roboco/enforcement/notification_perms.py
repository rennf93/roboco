"""
Notification Permission Enforcement

Validates who can send notifications to whom.
Only PMs, Board, and Auditor can send notifications.
"""

from roboco.agents_config import (
    CELL_MEMBERS,
    NOTIFICATION_PERMISSIONS,
    get_agent_cell,
    get_agent_role,
)
from roboco.exceptions import RobocoError


class NotificationPermissionError(RobocoError):
    """Raised when an agent doesn't have permission to send a notification."""

    def __init__(
        self,
        sender_id: str,
        recipient_id: str | None = None,
        message: str | None = None,
    ):
        self.sender_id = sender_id
        self.recipient_id = recipient_id
        super().__init__(
            code="NOTIFICATION_PERMISSION_DENIED",
            message=message or f"Agent {sender_id} cannot send notifications",
            details={
                "sender_id": sender_id,
                "recipient_id": recipient_id,
            },
        )


def _can_send_to_recipient(sender_id: str, recipient_id: str) -> tuple[bool, str]:
    """
    Check if sender can send notification to a specific recipient.

    Returns:
        Tuple of (can_send, reason)
    """
    role = get_agent_role(sender_id)
    permissions = NOTIFICATION_PERMISSIONS.get(role, {"can_send": False})

    if not permissions.get("can_send", False):
        return False, f"Agents with role '{role}' cannot send notifications"

    scope = permissions.get("scope", [])

    if scope == "all":
        return True, "OK"

    if scope == "cell":
        sender_cell = get_agent_cell(sender_id)
        recipient_cell = get_agent_cell(recipient_id)

        if sender_cell and sender_cell == recipient_cell:
            return True, "OK"
        return (
            False,
            f"Cell PM can only notify members of their own cell ({sender_cell})",
        )

    if isinstance(scope, list) and recipient_id in scope:
        return True, "OK"

    return False, f"Cannot send notifications to {recipient_id}"


def validate_notification_permission(
    sender_id: str,
    recipients: list[str],
) -> bool:
    """
    Validate sender can notify all recipients.

    Args:
        sender_id: The sending agent
        recipients: List of recipient agent IDs

    Returns:
        True if allowed

    Raises:
        NotificationPermissionError: If permission denied
    """
    role = get_agent_role(sender_id)
    permissions = NOTIFICATION_PERMISSIONS.get(role, {"can_send": False})

    # First check if sender can send at all
    if not permissions.get("can_send", False):
        raise NotificationPermissionError(
            sender_id=sender_id,
            message=f"Agents with role '{role}' cannot send notifications. "
            "Only PMs, Board members, and Auditor can send notifications.",
        )

    # Then check each recipient
    for recipient_id in recipients:
        can_send, reason = _can_send_to_recipient(sender_id, recipient_id)
        if not can_send:
            raise NotificationPermissionError(
                sender_id=sender_id,
                recipient_id=recipient_id,
                message=reason,
            )

    return True


def get_notification_scope(agent_id: str) -> dict:
    """
    Get the notification scope for an agent.

    Returns:
        Dict with can_send and scope information
    """
    role = get_agent_role(agent_id)
    return NOTIFICATION_PERMISSIONS.get(role, {"can_send": False})
