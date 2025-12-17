"""
Message MCP Server

Exposes messaging tools to Claude Code agents with built-in
enforcement of channel access rules.

Tools:
- roboco_message_send: Send a message to a channel
- roboco_message_list: List recent messages
- roboco_message_get: Get a specific message
- roboco_channel_list: List available channels
- roboco_channel_history: Get channel message history
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from fastapi import status
from mcp.server.fastmcp import FastMCP

from roboco.agents_config import CHANNEL_ACCESS, get_agent_role
from roboco.config import settings
from roboco.llm import ToonAdapter
from roboco.mcp.schemas import (
    AskQuestionInput,
    ReportBlockerInput,
    SendMessageInput,
)

# Global TOON adapter for encoding message data
_toon = ToonAdapter()


def _get_agent_headers(agent_id: str) -> dict[str, str]:
    """Get standard headers for API calls."""
    return {
        "X-Agent-Id": agent_id,
        "X-Agent-Role": get_agent_role(agent_id),
    }


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def _check_channel_access(agent_id: str, channel_slug: str, action: str) -> bool:
    """Check if agent has access to channel for the given action."""
    channel = CHANNEL_ACCESS.get(channel_slug, {})
    allowed = channel.get(action, [])

    if "*" in allowed:
        return True
    if agent_id in allowed:
        return True

    # Silent observers can always read
    return bool(action == "read" and agent_id in channel.get("silent", []))


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


def _validate_message_send(
    agent_id: str,
    channel_slug: str,
    content: str,
    message_type: str,
) -> dict[str, Any] | None:
    """Validate message send parameters. Returns error dict or None if valid."""
    valid_types = [
        "reasoning",
        "dialogue",
        "decision",
        "action",
        "blocker",
        "technical",
    ]
    if message_type not in valid_types:
        return _format_error_response(
            "INVALID_TYPE",
            f"Invalid message type '{message_type}'. Must be one of: {valid_types}",
        )

    if not _check_channel_access(agent_id, channel_slug, "write"):
        writable = [
            ch for ch in CHANNEL_ACCESS if _check_channel_access(agent_id, ch, "write")
        ]
        return _format_error_response(
            "ACCESS_DENIED",
            f"You don't have write access to #{channel_slug}",
            {"your_writable_channels": writable},
        )

    if agent_id in CHANNEL_ACCESS.get(channel_slug, {}).get("silent", []):
        return _format_error_response(
            "SILENT_OBSERVER",
            "You are a silent observer on this channel and cannot post messages.",
        )

    if not content or not content.strip():
        return _format_error_response(
            "EMPTY_CONTENT", "Message content cannot be empty."
        )

    return None


async def _get_default_group(
    client: httpx.AsyncClient,
    channel_id: str,
    headers: dict[str, str],
) -> str | dict[str, Any]:
    """Get the default (first) group for a channel. Returns group_id or error dict."""
    groups_resp = await client.get(
        f"{settings.internal_api_url}/channels/{channel_id}/groups",
        headers=headers,
    )

    if groups_resp.status_code != status.HTTP_200_OK:
        return _format_error_response(
            "GROUPS_ERROR",
            "Failed to get channel groups",
            {"status": groups_resp.status_code},
        )

    groups = groups_resp.json()
    if not groups:
        return _format_error_response("NO_GROUPS", "Channel has no groups")

    # Return first active group, or first group if none are active
    for group in groups:
        if group.get("is_active", True):
            return str(group["id"])
    return str(groups[0]["id"])


async def _get_or_create_session(
    client: httpx.AsyncClient,
    channel_id: str,
    headers: dict[str, str],
) -> str | dict[str, Any]:
    """Get or create session for channel. Returns session_id or error dict."""
    # First get the default group for this channel
    group_result = await _get_default_group(client, channel_id, headers)
    if isinstance(group_result, dict):
        return group_result  # Error response
    group_id = group_result

    # Check if group has an active session
    sessions_resp = await client.get(
        f"{settings.internal_api_url}/sessions",
        params={"group_id": group_id, "limit": 1},
        headers=headers,
    )

    if sessions_resp.status_code == status.HTTP_200_OK:
        data = sessions_resp.json()
        items = data.get("items", [])
        # Find an active session
        for session in items:
            if session.get("status") == "active":
                return str(session["id"])

    # Create new session
    create_resp = await client.post(
        f"{settings.internal_api_url}/sessions",
        json={"group_id": group_id},
        headers=headers,
    )
    if create_resp.status_code in [status.HTTP_200_OK, status.HTTP_201_CREATED]:
        return str(create_resp.json()["id"])

    return _format_error_response(
        "SESSION_ERROR",
        "Failed to create session",
        {"api_error": create_resp.text},
    )


# =============================================================================
# TOOL IMPLEMENTATIONS
# =============================================================================


async def _handle_channel_list(agent_id: str) -> dict[str, Any]:
    """Handle channel listing."""
    readable = []
    writable = []

    for channel_slug in CHANNEL_ACCESS:
        if _check_channel_access(agent_id, channel_slug, "read"):
            readable.append(channel_slug)
        if _check_channel_access(agent_id, channel_slug, "write"):
            writable.append(channel_slug)

    guidance = (
        f"You can read from {len(readable)} channel(s) and "
        f"write to {len(writable)} channel(s). "
        "Use roboco_message_send to post messages. "
        "Use roboco_channel_history to read recent messages."
    )

    return {
        "readable_channels": readable,
        "writable_channels": writable,
        "guidance": guidance,
    }


async def _handle_channel_history(  # noqa: PLR0911
    agent_id: str,
    channel_slug: str,
    limit: int,
    hours_back: int,
) -> dict[str, Any]:
    """Handle channel history retrieval."""
    if not _check_channel_access(agent_id, channel_slug, "read"):
        return _format_error_response(
            "ACCESS_DENIED", f"You don't have read access to #{channel_slug}"
        )

    limit = min(limit, 100)
    since = datetime.now(UTC) - timedelta(hours=hours_back)

    headers = _get_agent_headers(agent_id)
    async with httpx.AsyncClient() as client:
        # Get channel by slug
        channels_resp = await client.get(
            f"{settings.internal_api_url}/channels",
            params={"slug": channel_slug},
            headers=headers,
        )

        if channels_resp.status_code != status.HTTP_200_OK:
            return _format_error_response("API_ERROR", "Failed to fetch channels")

        channels_data = channels_resp.json()
        items = channels_data.get("items", channels_data)
        if not items:
            return _format_error_response(
                "NOT_FOUND", f"Channel #{channel_slug} not found"
            )

        channel = items[0] if isinstance(items, list) else items
        channel_id = channel["id"]

        # Get groups for this channel
        group_result = await _get_default_group(client, channel_id, headers)
        if isinstance(group_result, dict):
            return group_result  # Error response
        group_id = group_result

        # Get sessions for this group
        sessions_resp = await client.get(
            f"{settings.internal_api_url}/sessions",
            params={"group_id": group_id, "limit": 5},
            headers=headers,
        )

        if sessions_resp.status_code != status.HTTP_200_OK:
            return _format_error_response("API_ERROR", "Failed to fetch sessions")

        sessions_data = sessions_resp.json()
        sessions = sessions_data.get("items", [])

        if not sessions:
            return {
                "channel": channel_slug,
                "messages": [],
                "total": 0,
                "has_more": False,
                "since": since.isoformat(),
            }

        # Get messages from all recent sessions
        all_messages = []
        for session in sessions:
            session_id = session["id"]
            messages_resp = await client.get(
                f"{settings.internal_api_url}/messages",
                params={
                    "session_id": session_id,
                    "after": since.isoformat(),
                    "limit": limit,
                },
                headers=headers,
            )

            if messages_resp.status_code == status.HTTP_200_OK:
                msg_data = messages_resp.json()
                all_messages.extend(msg_data.get("items", []))

            if len(all_messages) >= limit:
                break

        # Sort by timestamp descending and limit
        all_messages.sort(key=lambda m: m.get("timestamp", ""), reverse=True)
        all_messages = all_messages[:limit]

    return {
        "channel": channel_slug,
        "messages": all_messages,
        "total": len(all_messages),
        "has_more": len(all_messages) >= limit,
        "since": since.isoformat(),
    }


async def _handle_message_send(
    agent_id: str,
    data: SendMessageInput,
) -> dict[str, Any]:
    """Handle message sending."""
    if validation_error := _validate_message_send(
        agent_id, data.channel_slug, data.content, data.message_type
    ):
        return validation_error

    headers = _get_agent_headers(agent_id)
    async with httpx.AsyncClient() as client:
        channels_resp = await client.get(
            f"{settings.internal_api_url}/channels",
            params={"slug": data.channel_slug},
            headers=headers,
        )

        if channels_resp.status_code != status.HTTP_200_OK or not channels_resp.json():
            return _format_error_response(
                "NOT_FOUND", f"Channel #{data.channel_slug} not found"
            )

        channel = channels_resp.json()[0]
        channel_id = channel["id"]

        session_result = await _get_or_create_session(client, channel_id, headers)
        if isinstance(session_result, dict):
            return session_result
        session_id = session_result

        message_data = {
            "session_id": session_id,
            "type": data.message_type,
            "content": data.content,
            "is_reply": data.reply_to is not None,
            "reply_to": data.reply_to,
            "mentions": data.mentions,
            "task_id": data.task_id,
        }

        send_resp = await client.post(
            f"{settings.internal_api_url}/messages",
            json=message_data,
            headers=headers,
        )

        if send_resp.status_code not in [status.HTTP_200_OK, status.HTTP_201_CREATED]:
            return _format_error_response(
                "SEND_FAILED", "Failed to send message", {"api_error": send_resp.text}
            )

        return {
            "status": "sent",
            "message": send_resp.json(),
            "channel": data.channel_slug,
            "guidance": "Message sent successfully.",
        }


async def _handle_message_get(message_id: str, agent_id: str) -> dict[str, Any]:
    """Handle message retrieval."""
    headers = _get_agent_headers(agent_id)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{settings.internal_api_url}/messages/{message_id}",
            headers=headers,
        )

        if resp.status_code == status.HTTP_404_NOT_FOUND:
            return _format_error_response(
                "NOT_FOUND", f"Message {message_id} not found"
            )

        if resp.status_code != status.HTTP_200_OK:
            return _format_error_response("API_ERROR", "Failed to fetch message")

        return {"message": resp.json()}


async def _handle_ask_question(
    data: AskQuestionInput,
    send_fn: Callable[[SendMessageInput], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Handle asking a question."""
    content = f"**Question**: {data.question}"
    if data.context:
        content = f"{data.context}\n\n{content}"

    msg_data = SendMessageInput(
        channel_slug=data.channel_slug,
        content=content,
        message_type="dialogue",
        task_id=data.task_id,
    )
    result = await send_fn(msg_data)

    if "error" in result:
        return result

    result["guidance"] = (
        "Question posted. You should now:\n"
        "1. Wait for an answer before proceeding with related work\n"
        "2. Check roboco_channel_history periodically for responses\n"
        "3. If urgent, consider mentioning the PM"
    )
    return result


async def _handle_report_blocker(
    data: ReportBlockerInput,
    send_fn: Callable[[SendMessageInput], Awaitable[dict[str, Any]]],
) -> dict[str, Any]:
    """Handle reporting a blocker."""
    content = (
        f"**BLOCKER**\n\n"
        f"**Issue**: {data.blocker_description}\n\n"
        f"**Needed to unblock**: {data.what_needed}"
    )

    msg_data = SendMessageInput(
        channel_slug=data.channel_slug,
        content=content,
        message_type="blocker",
        task_id=data.task_id,
    )
    result = await send_fn(msg_data)

    if "error" in result:
        return result

    result["guidance"] = (
        "Blocker reported. The PM will be notified.\n"
        "You should:\n"
        "1. Wait for resolution, or\n"
        "2. Switch to another task (call roboco_task_scan)"
    )
    return result


# =============================================================================
# MCP SERVER FACTORY
# =============================================================================


def create_message_mcp_server(agent_id: str) -> FastMCP:
    """
    Create a Message MCP server for a specific agent.

    Args:
        agent_id: The agent identifier (e.g., "be-dev-1")

    Returns:
        Configured FastMCP server
    """
    mcp = FastMCP(f"roboco-message-{agent_id}", json_response=True)

    @mcp.tool()
    async def roboco_channel_list() -> dict[str, Any]:
        """List channels you have access to."""
        return await _handle_channel_list(agent_id)

    @mcp.tool()
    async def roboco_channel_history(
        channel_slug: str,
        limit: int = 50,
        hours_back: int = 24,
    ) -> dict[str, Any]:
        """
        Get recent message history from a channel.

        You must have read access to the channel.
        """
        return await _handle_channel_history(agent_id, channel_slug, limit, hours_back)

    @mcp.tool()
    async def roboco_message_send(data: SendMessageInput) -> dict[str, Any]:
        """
        Send a message to a channel.

        You must have write access to the channel.
        """
        return await _handle_message_send(agent_id, data)

    @mcp.tool()
    async def roboco_message_get(message_id: str) -> dict[str, Any]:
        """Get a specific message by ID."""
        return await _handle_message_get(message_id, agent_id)

    @mcp.tool()
    async def roboco_ask_question(
        channel_slug: str,
        question: str,
        context: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Ask a question in a channel.

        After asking, wait for an answer before proceeding.
        """

        async def send_fn(d: SendMessageInput) -> dict[str, Any]:
            return await _handle_message_send(agent_id, d)

        data = AskQuestionInput(
            channel_slug=channel_slug,
            question=question,
            context=context,
            task_id=task_id,
        )
        return await _handle_ask_question(data, send_fn)

    @mcp.tool()
    async def roboco_report_blocker(
        channel_slug: str,
        blocker_description: str,
        what_needed: str,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Report a blocker in a channel.

        The PM will be notified automatically.
        """

        async def send_fn(d: SendMessageInput) -> dict[str, Any]:
            return await _handle_message_send(agent_id, d)

        data = ReportBlockerInput(
            channel_slug=channel_slug,
            blocker_description=blocker_description,
            what_needed=what_needed,
            task_id=task_id,
        )
        return await _handle_report_blocker(data, send_fn)

    return mcp


# =============================================================================
# STANDALONE RUNNER
# =============================================================================

if __name__ == "__main__":
    import sys

    MIN_ARGS = 2
    if len(sys.argv) < MIN_ARGS:
        print("Usage: python message_server.py <agent_id>")
        sys.exit(1)

    agent_id_arg = sys.argv[1]
    server = create_message_mcp_server(agent_id_arg)
    server.run()
