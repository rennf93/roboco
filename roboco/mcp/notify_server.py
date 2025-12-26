"""
Notify MCP Server

Exposes notification tools to Claude Code agents with built-in
enforcement of notification permissions.

Tools available to ALL agents:
- roboco_notify_list: List your notifications
- roboco_notify_get: Get a specific notification
- roboco_notify_ack: Acknowledge a notification

Tools available ONLY to PM/Board/Auditor:
- roboco_notify_send: Send a notification
- roboco_escalate: Escalate an issue (PMs only)
- roboco_request_approval: Request approval (PMs/Board only)

Note: Developers, QA, and Documenters do not see the sending tools.
They should use message channels and blocker reporting instead.
"""

from typing import Any

from fastapi import status
from mcp.server.fastmcp import FastMCP

from roboco.agents_config import (
    NOTIFICATION_PERMISSIONS,
    VALID_NOTIFICATION_PRIORITIES,
    VALID_NOTIFICATION_TYPES,
    can_send_notifications,
    get_agent_cell,
    get_agent_role,
)
from roboco.mcp.schemas import SendNotificationInput
from roboco.mcp.utils import ApiClient, format_error_response
from roboco.models.base import NotificationPriority, NotificationType

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def _check_cell_scope(sender_id: str) -> tuple[bool, str]:
    """Check if sender can notify within their cell."""
    sender_cell = get_agent_cell(sender_id)
    return sender_cell is not None, sender_cell or ""


def _can_send_notification(sender_id: str, recipient_id: str) -> tuple[bool, str]:
    """Check if sender can send notification to recipient."""
    role = get_agent_role(sender_id)
    permissions = NOTIFICATION_PERMISSIONS.get(role, {"can_send": False})

    can_send = False
    reason = ""

    if not permissions.get("can_send", False):
        reason = f"Agents with role '{role}' cannot send notifications"
    else:
        scope = permissions.get("scope", [])

        if scope == "all":
            can_send = True
            reason = "OK"
        elif scope == "cell":
            has_cell, sender_cell = _check_cell_scope(sender_id)
            recipient_cell = get_agent_cell(recipient_id)
            recipient_role = get_agent_role(recipient_id)

            # Cell PM can notify their own cell members
            if (has_cell and sender_cell == recipient_cell) or recipient_role in {
                "main_pm",
                "cell_pm",
            }:
                can_send = True
                reason = "OK"
            else:
                reason = "Cell PM can only notify cell members, Main PM, or other PMs"
        elif isinstance(scope, list) and recipient_id in scope:
            can_send = True
            reason = "OK"
        else:
            reason = f"You cannot send notifications to {recipient_id}"

    return can_send, reason


def _validate_notification_type(notification_type: str) -> dict[str, Any] | None:
    """Validate notification type. Returns error dict or None if valid."""
    if notification_type not in VALID_NOTIFICATION_TYPES:
        valid = sorted(VALID_NOTIFICATION_TYPES)
        return format_error_response(
            "INVALID_TYPE", f"Invalid type. Must be one of: {valid}"
        )
    return None


def _validate_priority(priority: str) -> dict[str, Any] | None:
    """Validate priority. Returns error dict or None if valid."""
    if priority not in VALID_NOTIFICATION_PRIORITIES:
        valid = sorted(VALID_NOTIFICATION_PRIORITIES)
        return format_error_response(
            "INVALID_PRIORITY",
            f"Invalid priority. Must be one of: {valid}",
        )
    return None


# =============================================================================
# TOOL IMPLEMENTATIONS
# =============================================================================


async def _handle_list(
    client: ApiClient,
    unread_only: bool,
    pending_ack_only: bool,
    limit: int,
) -> dict[str, Any]:
    """Handle notification listing."""
    params: dict[str, str | int] = {
        "unread_only": str(unread_only).lower(),
        "pending_ack_only": str(pending_ack_only).lower(),
        "limit": limit,
    }

    resp = await client.get("/notifications", params=params)
    if not resp.ok:
        return format_error_response("API_ERROR", "Failed to fetch notifications")

    data = resp.json()
    unread = data.get("unread_count", 0)
    pending_ack = data.get("pending_ack_count", 0)

    guidance_parts = []
    if pending_ack > 0:
        guidance_parts.append(
            f"You have {pending_ack} notification(s) requiring acknowledgment. "
            "Use roboco_notify_ack to acknowledge them."
        )
    if unread > 0:
        guidance_parts.append(f"You have {unread} unread notification(s).")
    if not guidance_parts:
        guidance_parts.append("No new notifications.")

    return {
        "notifications": data.get("items", []),
        "total": data.get("total", 0),
        "unread_count": unread,
        "pending_ack_count": pending_ack,
        "guidance": " ".join(guidance_parts),
    }


async def _handle_get(client: ApiClient, notification_id: str) -> dict[str, Any]:
    """Handle getting a specific notification."""
    resp = await client.get(f"/notifications/{notification_id}")

    if resp.is_status(status.HTTP_404_NOT_FOUND):
        return format_error_response("NOT_FOUND", "Notification not found")

    if resp.is_status(status.HTTP_403_FORBIDDEN):
        return format_error_response(
            "NOT_RECIPIENT", "You are not a recipient of this notification"
        )

    if not resp.ok:
        return format_error_response("API_ERROR", "Failed to fetch notification")

    notification = resp.json()
    guidance = ""
    if notification.get("requires_ack") and not notification.get("is_acknowledged"):
        guidance = (
            "This notification requires acknowledgment. "
            "Use roboco_notify_ack to acknowledge."
        )

    return {"notification": notification, "guidance": guidance}


async def _handle_ack(client: ApiClient, notification_id: str) -> dict[str, Any]:
    """Handle acknowledging a notification."""
    resp = await client.post(f"/notifications/{notification_id}/ack")

    if resp.is_status(status.HTTP_404_NOT_FOUND):
        return format_error_response("NOT_FOUND", "Notification not found")

    if resp.is_status(status.HTTP_403_FORBIDDEN):
        return format_error_response(
            "NOT_RECIPIENT", "You are not a recipient of this notification"
        )

    if resp.is_status(status.HTTP_400_BAD_REQUEST):
        return format_error_response(
            "NO_ACK_REQUIRED", "This notification does not require acknowledgment"
        )

    if not resp.ok:
        return format_error_response("API_ERROR", "Failed to acknowledge notification")

    notification = resp.json()
    return {
        "status": "acknowledged",
        "notification": notification,
        "guidance": "Notification acknowledged. The sender will be informed.",
    }


def _check_send_permission(agent_id: str) -> dict[str, Any] | None:
    """Check if agent has permission to send notifications."""
    role = get_agent_role(agent_id)
    permissions = NOTIFICATION_PERMISSIONS.get(role, {"can_send": False})
    if not permissions.get("can_send", False):
        return format_error_response(
            "NOT_AUTHORIZED",
            f"Agents with role '{role}' cannot send notifications. "
            "Only PMs, Board members, and Auditor can send notifications.",
            {"your_role": role},
        )
    return None


def _check_recipients(agent_id: str, recipients: list[str]) -> dict[str, Any] | None:
    """Check if agent can send to all recipients."""
    denied = [
        {"recipient": r, "reason": reason}
        for r in recipients
        for can_send, reason in [_can_send_notification(agent_id, r)]
        if not can_send
    ]
    if denied:
        return format_error_response(
            "RECIPIENT_DENIED",
            "Cannot send to one or more recipients",
            {"denied": denied},
        )
    return None


def _validate_send_input(
    agent_id: str, data: SendNotificationInput
) -> dict[str, Any] | None:
    """Validate all send notification inputs. Returns error or None."""
    for check in [
        lambda: _check_send_permission(agent_id),
        lambda: _check_recipients(agent_id, data.recipients),
        lambda: _validate_notification_type(data.notification_type),
        lambda: _validate_priority(data.priority),
    ]:
        if error := check():
            return error
    return None


async def _resolve_recipients(
    recipients: list[str], client: "ApiClient"
) -> tuple[list[str], dict[str, Any] | None]:
    """Resolve recipient slugs to UUIDs. Returns (resolved_list, error_or_none)."""
    from roboco.mcp.utils import resolve_agent_uuid_cached

    resolved: list[str] = []
    unresolved: list[str] = []
    for recipient in recipients:
        uuid = await resolve_agent_uuid_cached(recipient, client)
        if uuid:
            resolved.append(uuid)
        else:
            unresolved.append(recipient)

    if unresolved:
        return [], format_error_response(
            "RECIPIENT_NOT_FOUND",
            f"Could not resolve recipient(s): {', '.join(unresolved)}",
        )
    return resolved, None


async def _handle_send(
    client: ApiClient, agent_id: str, data: SendNotificationInput
) -> dict[str, Any]:
    """Handle sending a notification."""
    if error := _validate_send_input(agent_id, data):
        return error

    resolved_recipients, error = await _resolve_recipients(data.recipients, client)
    if error:
        return error

    payload = {
        "type": data.notification_type,
        "priority": data.priority,
        "to_agents": resolved_recipients,
        "subject": data.subject,
        "body": data.body,
        "requires_ack": data.requires_ack,
        "related_task_id": data.related_task_id,
    }

    resp = await client.post("/notifications", json=payload)

    if not resp.ok:
        return format_error_response(
            "SEND_FAILED", "Failed to send notification", {"api_error": resp.text}
        )

    notification = resp.json()
    ack_note = "Recipients must acknowledge." if data.requires_ack else ""
    count = len(data.recipients)

    return {
        "status": "sent",
        "notification": notification,
        "recipients_count": count,
        "guidance": f"Notification sent to {count} recipient(s). {ack_note}".strip(),
    }


# =============================================================================
# MCP SERVER FACTORY
# =============================================================================


def create_notify_mcp_server(agent_id: str) -> FastMCP:
    """
    Create a Notify MCP server for a specific agent.

    Args:
        agent_id: The agent identifier (e.g., "be-pm")

    Returns:
        Configured FastMCP server
    """
    mcp = FastMCP(f"roboco-notify-{agent_id}", json_response=True)

    # Create shared API client for this agent
    client = ApiClient(agent_id)

    @mcp.tool()
    async def roboco_notify_list(
        unread_only: bool = False,
        pending_ack_only: bool = False,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List your notifications."""
        return await _handle_list(client, unread_only, pending_ack_only, limit)

    @mcp.tool()
    async def roboco_notify_get(notification_id: str) -> dict[str, Any]:
        """Get a specific notification. Also marks it as read."""
        return await _handle_get(client, notification_id)

    @mcp.tool()
    async def roboco_notify_ack(notification_id: str) -> dict[str, Any]:
        """Acknowledge a notification."""
        return await _handle_ack(client, notification_id)

    # Only register send/escalate/approval tools for agents who can send notifications
    # This prevents developers, QA, and documenters from even seeing these tools
    if can_send_notifications(agent_id):

        @mcp.tool()
        async def roboco_notify_send(data: SendNotificationInput) -> dict[str, Any]:
            """
            Send a notification to one or more agents.

            Cell PMs can only notify their own cell.
            Main PM, Board, and Auditor can notify anyone.
            """
            return await _handle_send(client, agent_id, data)

        role = get_agent_role(agent_id)

        # Only PMs can escalate
        if role in ["cell_pm", "main_pm"]:

            @mcp.tool()
            async def roboco_escalate(
                escalate_to: str,
                subject: str,
                description: str,
                task_id: str | None = None,
            ) -> dict[str, Any]:
                """
                Escalate an issue to a higher level.

                Sends a high-priority notification requiring acknowledgment.
                """
                input_data = SendNotificationInput(
                    recipients=[escalate_to],
                    subject=f"[ESCALATION] {subject}",
                    body=description,
                    notification_type=NotificationType.BLOCKER_ESCALATION.value,
                    priority=NotificationPriority.HIGH.value,
                    requires_ack=True,
                    related_task_id=task_id,
                )
                return await _handle_send(client, agent_id, input_data)

        # Only PMs and Board can request approvals
        if role in ["cell_pm", "main_pm", "product_owner", "head_marketing"]:

            @mcp.tool()
            async def roboco_request_approval(
                approver: str,
                subject: str,
                what_needs_approval: str,
                task_id: str | None = None,
            ) -> dict[str, Any]:
                """
                Request approval from someone.
                """
                input_data = SendNotificationInput(
                    recipients=[approver],
                    subject=f"[APPROVAL NEEDED] {subject}",
                    body=what_needs_approval,
                    notification_type=NotificationType.REVIEW_REQUEST.value,
                    priority=NotificationPriority.NORMAL.value,
                    requires_ack=True,
                    related_task_id=task_id,
                )
                return await _handle_send(client, agent_id, input_data)

    return mcp


# =============================================================================
# STANDALONE RUNNER
# =============================================================================

if __name__ == "__main__":
    import sys

    MIN_ARGS = 2
    if len(sys.argv) < MIN_ARGS:
        print("Usage: python notify_server.py <agent_id>")
        sys.exit(1)

    agent_id_arg = sys.argv[1]
    server = create_notify_mcp_server(agent_id_arg)
    server.run()
