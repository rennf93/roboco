"""
Notify MCP Server

Exposes notification tools to Claude Code agents with built-in
enforcement of notification permissions.

Tools:
- roboco_notify_list: List your notifications
- roboco_notify_get: Get a specific notification
- roboco_notify_ack: Acknowledge a notification
- roboco_notify_send: Send a notification (PM/Board/Auditor only)
"""

from typing import Any

import httpx
from fastapi import status
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from roboco.agents_config import (
    NOTIFICATION_PERMISSIONS,
    get_agent_cell,
    get_agent_role,
)
from roboco.config import settings

# =============================================================================
# INPUT MODELS
# =============================================================================


class SendNotificationInput(BaseModel):
    """Input for sending a notification."""

    recipients: list[str] = Field(..., description="Agent IDs to notify")
    subject: str = Field(..., description="Notification subject")
    body: str = Field(..., description="Notification body")
    notification_type: str = Field(
        default="info", description="Type: info, alert, task, escalation, approval"
    )
    priority: str = Field(default="normal", description="low, normal, high, urgent")
    requires_ack: bool = Field(default=True, description="Require acknowledgment")
    related_task_id: str | None = Field(default=None, description="Related task")


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def _can_send_notification(sender_id: str, recipient_id: str) -> tuple[bool, str]:
    """Check if sender can send notification to recipient."""
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

    return False, f"You cannot send notifications to {recipient_id}"


def _get_api_url() -> str:
    """Get the RoboCo API base URL."""
    return f"http://{settings.host}:{settings.port}/api/v1"


def _format_error_response(
    error_code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Format a standardized error response."""
    return {
        "error": {
            "code": error_code,
            "message": message,
            "details": details or {},
        }
    }


# =============================================================================
# TOOL IMPLEMENTATIONS
# =============================================================================


async def _handle_list(
    agent_id: str,
    unread_only: bool,
    pending_ack_only: bool,
    limit: int,
) -> dict[str, Any]:
    """Handle notification listing."""
    async with httpx.AsyncClient() as client:
        params: dict[str, str | int] = {
            "unread_only": str(unread_only).lower(),
            "pending_ack_only": str(pending_ack_only).lower(),
            "limit": limit,
        }

        resp = await client.get(
            f"{_get_api_url()}/notifications",
            params=params,
            headers={"X-Agent-Id": agent_id},
        )

        if resp.status_code != status.HTTP_200_OK:
            return _format_error_response("API_ERROR", "Failed to fetch notifications")

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


async def _handle_get(agent_id: str, notification_id: str) -> dict[str, Any]:
    """Handle getting a specific notification."""
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{_get_api_url()}/notifications/{notification_id}",
            headers={"X-Agent-Id": agent_id},
        )

        if resp.status_code == status.HTTP_404_NOT_FOUND:
            return _format_error_response("NOT_FOUND", "Notification not found")

        if resp.status_code == status.HTTP_403_FORBIDDEN:
            return _format_error_response(
                "NOT_RECIPIENT", "You are not a recipient of this notification"
            )

        if resp.status_code != status.HTTP_200_OK:
            return _format_error_response("API_ERROR", "Failed to fetch notification")

        notification = resp.json()

    guidance = ""
    if notification.get("requires_ack") and not notification.get("is_acknowledged"):
        guidance = (
            "This notification requires acknowledgment. "
            "Use roboco_notify_ack to acknowledge."
        )

    return {"notification": notification, "guidance": guidance}


async def _handle_ack(agent_id: str, notification_id: str) -> dict[str, Any]:
    """Handle acknowledging a notification."""
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{_get_api_url()}/notifications/{notification_id}/ack",
            headers={"X-Agent-Id": agent_id},
        )

        if resp.status_code == status.HTTP_404_NOT_FOUND:
            return _format_error_response("NOT_FOUND", "Notification not found")

        if resp.status_code == status.HTTP_403_FORBIDDEN:
            return _format_error_response(
                "NOT_RECIPIENT", "You are not a recipient of this notification"
            )

        if resp.status_code == status.HTTP_400_BAD_REQUEST:
            return _format_error_response(
                "NO_ACK_REQUIRED", "This notification does not require acknowledgment"
            )

        if resp.status_code != status.HTTP_200_OK:
            return _format_error_response(
                "API_ERROR", "Failed to acknowledge notification"
            )

        notification = resp.json()

    return {
        "status": "acknowledged",
        "notification": notification,
        "guidance": "Notification acknowledged. The sender will be informed.",
    }


async def _handle_send(agent_id: str, data: SendNotificationInput) -> dict[str, Any]:
    """Handle sending a notification."""
    role = get_agent_role(agent_id)
    permissions = NOTIFICATION_PERMISSIONS.get(role, {"can_send": False})

    if not permissions.get("can_send", False):
        return _format_error_response(
            "NOT_AUTHORIZED",
            f"Agents with role '{role}' cannot send notifications. "
            "Only PMs, Board members, and Auditor can send notifications.",
            {"your_role": role},
        )

    denied_recipients = []
    for recipient in data.recipients:
        can_send, reason = _can_send_notification(agent_id, recipient)
        if not can_send:
            denied_recipients.append({"recipient": recipient, "reason": reason})

    if denied_recipients:
        return _format_error_response(
            "RECIPIENT_DENIED",
            "Cannot send to one or more recipients",
            {"denied": denied_recipients},
        )

    valid_types = ["info", "alert", "task", "escalation", "approval"]
    if data.notification_type not in valid_types:
        return _format_error_response(
            "INVALID_TYPE", f"Invalid notification type. Must be one of: {valid_types}"
        )

    valid_priorities = ["low", "normal", "high", "urgent"]
    if data.priority not in valid_priorities:
        return _format_error_response(
            "INVALID_PRIORITY", f"Invalid priority. Must be one of: {valid_priorities}"
        )

    async with httpx.AsyncClient() as client:
        payload = {
            "type": data.notification_type,
            "priority": data.priority,
            "to_agents": data.recipients,
            "subject": data.subject,
            "body": data.body,
            "requires_ack": data.requires_ack,
            "related_task_id": data.related_task_id,
        }

        resp = await client.post(
            f"{_get_api_url()}/notifications",
            json=payload,
            headers={"X-Agent-Id": agent_id},
        )

        if resp.status_code not in [status.HTTP_200_OK, status.HTTP_201_CREATED]:
            return _format_error_response(
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

    @mcp.tool()
    async def roboco_notify_list(
        unread_only: bool = False,
        pending_ack_only: bool = False,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List your notifications."""
        return await _handle_list(agent_id, unread_only, pending_ack_only, limit)

    @mcp.tool()
    async def roboco_notify_get(notification_id: str) -> dict[str, Any]:
        """Get a specific notification. Also marks it as read."""
        return await _handle_get(agent_id, notification_id)

    @mcp.tool()
    async def roboco_notify_ack(notification_id: str) -> dict[str, Any]:
        """Acknowledge a notification."""
        return await _handle_ack(agent_id, notification_id)

    @mcp.tool()
    async def roboco_notify_send(data: SendNotificationInput) -> dict[str, Any]:
        """
        Send a notification to one or more agents.

        Only PMs, Board members, and Auditor can send notifications.
        Cell PMs can only notify their own cell.
        """
        return await _handle_send(agent_id, data)

    @mcp.tool()
    async def roboco_escalate(
        escalate_to: str,
        subject: str,
        description: str,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Escalate an issue to a higher level (PM only).

        Sends a high-priority notification requiring acknowledgment.
        """
        role = get_agent_role(agent_id)
        if role not in ["cell_pm", "main_pm"]:
            return _format_error_response(
                "NOT_PM", "Only PMs can use the escalate function"
            )

        input_data = SendNotificationInput(
            recipients=[escalate_to],
            subject=f"[ESCALATION] {subject}",
            body=description,
            notification_type="escalation",
            priority="high",
            requires_ack=True,
            related_task_id=task_id,
        )
        return await _handle_send(agent_id, input_data)

    @mcp.tool()
    async def roboco_request_approval(
        approver: str,
        subject: str,
        what_needs_approval: str,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Request approval from someone (PM/Board only).
        """
        role = get_agent_role(agent_id)
        if role not in ["cell_pm", "main_pm", "product_owner", "head_marketing"]:
            return _format_error_response(
                "NOT_AUTHORIZED", "Only PMs and Board can request approvals"
            )

        input_data = SendNotificationInput(
            recipients=[approver],
            subject=f"[APPROVAL NEEDED] {subject}",
            body=what_needs_approval,
            notification_type="approval",
            priority="normal",
            requires_ack=True,
            related_task_id=task_id,
        )
        return await _handle_send(agent_id, input_data)

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
