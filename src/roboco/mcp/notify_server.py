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

from datetime import datetime
from typing import Any
from uuid import UUID

import httpx
from mcp.server.fastmcp import FastMCP

from roboco.agents_config import (
    NOTIFICATION_PERMISSIONS,
    get_agent_cell,
    get_agent_role,
)
from roboco.config import settings


def _can_send_notification(sender_id: str, recipient_id: str) -> tuple[bool, str]:
    """
    Check if sender can send notification to recipient.

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

    return False, f"You cannot send notifications to {recipient_id}"


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


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
# MCP SERVER FACTORY
# =============================================================================


def create_notify_mcp_server(agent_id: str) -> FastMCP:
    """
    Create a Notify MCP server for a specific agent.

    The agent_id is embedded in the server to enforce permissions.

    Args:
        agent_id: The agent identifier (e.g., "be-pm")

    Returns:
        Configured FastMCP server
    """
    mcp = FastMCP(f"roboco-notify-{agent_id}", json_response=True)

    # Store agent context
    mcp.agent_id = agent_id  # type: ignore

    # =========================================================================
    # LIST NOTIFICATIONS
    # =========================================================================

    @mcp.tool()
    async def roboco_notify_list(
        unread_only: bool = False,
        pending_ack_only: bool = False,
        limit: int = 50,
    ) -> dict[str, Any]:
        """
        List your notifications.

        Args:
            unread_only: Only show unread notifications
            pending_ack_only: Only show notifications pending acknowledgment
            limit: Maximum notifications to return

        Returns:
            List of notifications with counts
        """
        async with httpx.AsyncClient() as client:
            params = {
                "unread_only": str(unread_only).lower(),
                "pending_ack_only": str(pending_ack_only).lower(),
                "limit": limit,
            }

            resp = await client.get(
                f"{_get_api_url()}/notifications",
                params=params,
                headers={"X-Agent-Id": agent_id},
            )

            if resp.status_code != 200:
                return _format_error_response(
                    "API_ERROR", "Failed to fetch notifications"
                )

            data = resp.json()

        # Add guidance based on counts
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

    # =========================================================================
    # GET NOTIFICATION
    # =========================================================================

    @mcp.tool()
    async def roboco_notify_get(notification_id: str) -> dict[str, Any]:
        """
        Get a specific notification.

        This also marks the notification as read.

        Args:
            notification_id: The notification UUID

        Returns:
            Notification details
        """
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{_get_api_url()}/notifications/{notification_id}",
                headers={"X-Agent-Id": agent_id},
            )

            if resp.status_code == 404:
                return _format_error_response("NOT_FOUND", "Notification not found")

            if resp.status_code == 403:
                return _format_error_response(
                    "NOT_RECIPIENT",
                    "You are not a recipient of this notification",
                )

            if resp.status_code != 200:
                return _format_error_response(
                    "API_ERROR", "Failed to fetch notification"
                )

            notification = resp.json()

        guidance = ""
        if notification.get("requires_ack") and not notification.get("is_acknowledged"):
            guidance = (
                "This notification requires acknowledgment. "
                "Use roboco_notify_ack to acknowledge."
            )

        return {
            "notification": notification,
            "guidance": guidance,
        }

    # =========================================================================
    # ACKNOWLEDGE NOTIFICATION
    # =========================================================================

    @mcp.tool()
    async def roboco_notify_ack(notification_id: str) -> dict[str, Any]:
        """
        Acknowledge a notification.

        Some notifications require acknowledgment to confirm receipt
        and understanding.

        Args:
            notification_id: The notification UUID

        Returns:
            Updated notification
        """
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{_get_api_url()}/notifications/{notification_id}/ack",
                headers={"X-Agent-Id": agent_id},
            )

            if resp.status_code == 404:
                return _format_error_response("NOT_FOUND", "Notification not found")

            if resp.status_code == 403:
                return _format_error_response(
                    "NOT_RECIPIENT",
                    "You are not a recipient of this notification",
                )

            if resp.status_code == 400:
                return _format_error_response(
                    "NO_ACK_REQUIRED",
                    "This notification does not require acknowledgment",
                )

            if resp.status_code != 200:
                return _format_error_response(
                    "API_ERROR", "Failed to acknowledge notification"
                )

            notification = resp.json()

        return {
            "status": "acknowledged",
            "notification": notification,
            "guidance": "Notification acknowledged. The sender will be informed.",
        }

    # =========================================================================
    # SEND NOTIFICATION (PM/Board/Auditor only)
    # =========================================================================

    @mcp.tool()
    async def roboco_notify_send(
        recipients: list[str],
        subject: str,
        body: str,
        notification_type: str = "info",
        priority: str = "normal",
        requires_ack: bool = True,
        related_task_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Send a notification to one or more agents.

        ENFORCEMENT:
        - Only PMs, Board members, and Auditor can send notifications
        - Cell PMs can only notify their own cell
        - Developers, QA, and Documenters CANNOT send notifications

        Args:
            recipients: List of agent IDs to notify
            subject: Notification subject
            body: Notification body
            notification_type: Type (info, alert, task, escalation, approval)
            priority: Priority (low, normal, high, urgent)
            requires_ack: Whether recipients must acknowledge
            related_task_id: Optional related task

        Returns:
            Sent notification or error
        """
        # Check sender permissions
        role = get_agent_role(agent_id)
        permissions = NOTIFICATION_PERMISSIONS.get(role, {"can_send": False})

        if not permissions.get("can_send", False):
            return _format_error_response(
                "NOT_AUTHORIZED",
                f"Agents with role '{role}' cannot send notifications. "
                "Only PMs, Board members, and Auditor can send notifications.",
                {"your_role": role},
            )

        # Check each recipient
        denied_recipients = []
        for recipient in recipients:
            can_send, reason = _can_send_notification(agent_id, recipient)
            if not can_send:
                denied_recipients.append({"recipient": recipient, "reason": reason})

        if denied_recipients:
            return _format_error_response(
                "RECIPIENT_DENIED",
                "Cannot send to one or more recipients",
                {"denied": denied_recipients},
            )

        # Validate notification type
        valid_types = ["info", "alert", "task", "escalation", "approval"]
        if notification_type not in valid_types:
            return _format_error_response(
                "INVALID_TYPE",
                f"Invalid notification type. Must be one of: {valid_types}",
            )

        # Validate priority
        valid_priorities = ["low", "normal", "high", "urgent"]
        if priority not in valid_priorities:
            return _format_error_response(
                "INVALID_PRIORITY",
                f"Invalid priority. Must be one of: {valid_priorities}",
            )

        async with httpx.AsyncClient() as client:
            payload = {
                "type": notification_type,
                "priority": priority,
                "to_agents": recipients,
                "subject": subject,
                "body": body,
                "requires_ack": requires_ack,
                "related_task_id": related_task_id,
            }

            resp = await client.post(
                f"{_get_api_url()}/notifications",
                json=payload,
                headers={"X-Agent-Id": agent_id},
            )

            if resp.status_code not in [200, 201]:
                return _format_error_response(
                    "SEND_FAILED",
                    "Failed to send notification",
                    {"api_error": resp.text},
                )

            notification = resp.json()

        ack_note = "Recipients must acknowledge." if requires_ack else ""

        return {
            "status": "sent",
            "notification": notification,
            "recipients_count": len(recipients),
            "guidance": f"Notification sent to {len(recipients)} recipient(s). {ack_note}",
        }

    # =========================================================================
    # CONVENIENCE: ESCALATE (PM only)
    # =========================================================================

    @mcp.tool()
    async def roboco_escalate(
        escalate_to: str,
        subject: str,
        description: str,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Escalate an issue to a higher level (PM convenience wrapper).

        This sends a high-priority notification requiring acknowledgment.

        Args:
            escalate_to: Agent ID to escalate to (e.g., "main-pm")
            subject: Escalation subject
            description: Detailed description of the issue
            task_id: Optional related task

        Returns:
            Sent escalation notification
        """
        role = get_agent_role(agent_id)
        if role not in ["cell_pm", "main_pm"]:
            return _format_error_response(
                "NOT_PM",
                "Only PMs can use the escalate function",
            )

        return await roboco_notify_send(
            recipients=[escalate_to],
            subject=f"[ESCALATION] {subject}",
            body=description,
            notification_type="escalation",
            priority="high",
            requires_ack=True,
            related_task_id=task_id,
        )

    # =========================================================================
    # CONVENIENCE: REQUEST APPROVAL (PM/Board only)
    # =========================================================================

    @mcp.tool()
    async def roboco_request_approval(
        approver: str,
        subject: str,
        what_needs_approval: str,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Request approval from someone (PM/Board convenience wrapper).

        Args:
            approver: Agent ID to request approval from
            subject: Approval subject
            what_needs_approval: Description of what needs approval
            task_id: Optional related task

        Returns:
            Sent approval request notification
        """
        role = get_agent_role(agent_id)
        if role not in ["cell_pm", "main_pm", "product_owner", "head_marketing"]:
            return _format_error_response(
                "NOT_AUTHORIZED",
                "Only PMs and Board can request approvals",
            )

        return await roboco_notify_send(
            recipients=[approver],
            subject=f"[APPROVAL NEEDED] {subject}",
            body=what_needs_approval,
            notification_type="approval",
            priority="normal",
            requires_ack=True,
            related_task_id=task_id,
        )

    return mcp


# =============================================================================
# STANDALONE RUNNER
# =============================================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python notify_server.py <agent_id>")
        sys.exit(1)

    agent_id = sys.argv[1]
    server = create_notify_mcp_server(agent_id)
    server.run()
