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

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from mcp.server.fastmcp import FastMCP

from roboco.agents_config import CHANNEL_ACCESS
from roboco.config import settings


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


def create_message_mcp_server(agent_id: str) -> FastMCP:
    """
    Create a Message MCP server for a specific agent.

    The agent_id is embedded in the server to enforce access rules.

    Args:
        agent_id: The agent identifier (e.g., "be-dev-1")

    Returns:
        Configured FastMCP server
    """
    mcp = FastMCP(f"roboco-message-{agent_id}", json_response=True)

    # Store agent context
    mcp.agent_id = agent_id  # type: ignore

    # =========================================================================
    # CHANNEL LISTING
    # =========================================================================

    @mcp.tool()
    async def roboco_channel_list() -> dict[str, Any]:
        """
        List channels you have access to.

        Returns:
            Dict with readable and writable channels
        """
        readable = []
        writable = []

        for channel_slug, _access in CHANNEL_ACCESS.items():
            if _check_channel_access(agent_id, channel_slug, "read"):
                readable.append(channel_slug)
            if _check_channel_access(agent_id, channel_slug, "write"):
                writable.append(channel_slug)

        return {
            "readable_channels": readable,
            "writable_channels": writable,
            "guidance": (
                f"You can read from {len(readable)} channel(s) and write to {len(writable)} channel(s). "
                "Use roboco_message_send to post messages. "
                "Use roboco_channel_history to read recent messages."
            ),
        }

    # =========================================================================
    # CHANNEL HISTORY
    # =========================================================================

    @mcp.tool()
    async def roboco_channel_history(
        channel_slug: str,
        limit: int = 50,
        hours_back: int = 24,
    ) -> dict[str, Any]:
        """
        Get recent message history from a channel.

        ENFORCEMENT:
        - You must have read access to the channel

        Args:
            channel_slug: The channel slug (e.g., "backend-cell")
            limit: Maximum messages to return (default 50, max 100)
            hours_back: How many hours back to look (default 24)

        Returns:
            List of messages with metadata
        """
        # Check read access
        if not _check_channel_access(agent_id, channel_slug, "read"):
            return _format_error_response(
                "ACCESS_DENIED",
                f"You don't have read access to #{channel_slug}",
            )

        limit = min(limit, 100)
        since = datetime.utcnow() - timedelta(hours=hours_back)

        async with httpx.AsyncClient() as client:
            # Get channel ID from slug
            channels_resp = await client.get(
                f"{_get_api_url()}/channels",
                params={"slug": channel_slug},
            )

            if channels_resp.status_code != 200:
                return _format_error_response("API_ERROR", "Failed to fetch channels")

            channels = channels_resp.json()
            if not channels:
                return _format_error_response(
                    "NOT_FOUND", f"Channel #{channel_slug} not found"
                )

            channel_id = channels[0]["id"]

            # Get messages
            messages_resp = await client.get(
                f"{_get_api_url()}/channels/{channel_id}/messages",
                params={
                    "after": since.isoformat(),
                    "limit": limit,
                },
            )

            if messages_resp.status_code != 200:
                return _format_error_response("API_ERROR", "Failed to fetch messages")

            messages = messages_resp.json()

        return {
            "channel": channel_slug,
            "messages": messages.get("items", []),
            "total": messages.get("total", 0),
            "has_more": messages.get("has_more", False),
            "since": since.isoformat(),
        }

    # =========================================================================
    # SEND MESSAGE
    # =========================================================================

    @mcp.tool()
    async def roboco_message_send(
        channel_slug: str,
        content: str,
        message_type: str = "dialogue",
        task_id: str | None = None,
        reply_to: str | None = None,
        mentions: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Send a message to a channel.

        ENFORCEMENT:
        - You must have write access to the channel
        - Message type must be valid
        - Content is required

        Args:
            channel_slug: The channel slug (e.g., "backend-cell")
            content: Message content
            message_type: Type of message (reasoning, dialogue, decision, action, blocker, technical)
            task_id: Optional task ID this message relates to
            reply_to: Optional message ID to reply to
            mentions: Optional list of agent IDs to mention (adds @agent-id)

        Returns:
            Sent message with confirmation
        """
        # Validate message type
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

        # Check write access
        if not _check_channel_access(agent_id, channel_slug, "write"):
            return _format_error_response(
                "ACCESS_DENIED",
                f"You don't have write access to #{channel_slug}",
                {
                    "your_writable_channels": [
                        ch
                        for ch in CHANNEL_ACCESS
                        if _check_channel_access(agent_id, ch, "write")
                    ]
                },
            )

        # Silent observers cannot write even if in read list
        if agent_id in CHANNEL_ACCESS.get(channel_slug, {}).get("silent", []):
            return _format_error_response(
                "SILENT_OBSERVER",
                "You are a silent observer on this channel and cannot post messages.",
            )

        if not content or not content.strip():
            return _format_error_response(
                "EMPTY_CONTENT",
                "Message content cannot be empty.",
            )

        async with httpx.AsyncClient() as client:
            # Get channel and active session
            channels_resp = await client.get(
                f"{_get_api_url()}/channels",
                params={"slug": channel_slug},
            )

            if channels_resp.status_code != 200 or not channels_resp.json():
                return _format_error_response(
                    "NOT_FOUND", f"Channel #{channel_slug} not found"
                )

            channel = channels_resp.json()[0]
            channel_id = channel["id"]

            # Get or create session for the channel
            session_resp = await client.get(
                f"{_get_api_url()}/channels/{channel_id}/session",
            )

            if session_resp.status_code != 200:
                # Create a new session
                create_resp = await client.post(
                    f"{_get_api_url()}/sessions",
                    json={"channel_id": channel_id},
                )
                if create_resp.status_code not in [200, 201]:
                    return _format_error_response(
                        "SESSION_ERROR", "Failed to get or create session"
                    )
                session_id = create_resp.json()["id"]
            else:
                session_id = session_resp.json()["id"]

            # Build message payload
            message_data = {
                "session_id": session_id,
                "type": message_type,
                "content": content,
                "is_reply": reply_to is not None,
                "reply_to": reply_to,
                "mentions": mentions or [],
                "task_id": task_id,
            }

            # Send message
            send_resp = await client.post(
                f"{_get_api_url()}/messages",
                json=message_data,
                headers={"X-Agent-Id": agent_id},
            )

            if send_resp.status_code not in [200, 201]:
                return _format_error_response(
                    "SEND_FAILED",
                    "Failed to send message",
                    {"api_error": send_resp.text},
                )

            message = send_resp.json()

        return {
            "status": "sent",
            "message": message,
            "channel": channel_slug,
            "guidance": "Message sent successfully.",
        }

    # =========================================================================
    # GET MESSAGE
    # =========================================================================

    @mcp.tool()
    async def roboco_message_get(message_id: str) -> dict[str, Any]:
        """
        Get a specific message by ID.

        Args:
            message_id: The message UUID

        Returns:
            Message details
        """
        async with httpx.AsyncClient() as client:
            resp = await client.get(f"{_get_api_url()}/messages/{message_id}")

            if resp.status_code == 404:
                return _format_error_response(
                    "NOT_FOUND", f"Message {message_id} not found"
                )

            if resp.status_code != 200:
                return _format_error_response("API_ERROR", "Failed to fetch message")

            message = resp.json()

        return {
            "message": message,
        }

    # =========================================================================
    # ASK QUESTION (convenience wrapper)
    # =========================================================================

    @mcp.tool()
    async def roboco_ask_question(
        channel_slug: str,
        question: str,
        context: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Ask a question in a channel (convenience wrapper).

        This is a common pattern - asking for clarification. The message
        is automatically formatted as a question.

        IMPORTANT: After asking, you should wait for an answer before
        proceeding with work that depends on this question.

        Args:
            channel_slug: The channel to ask in
            question: The question to ask
            context: Optional context for the question
            task_id: Optional task this relates to

        Returns:
            Sent question message
        """
        content = f"**Question**: {question}"
        if context:
            content = f"{context}\n\n{content}"

        result = await roboco_message_send(
            channel_slug=channel_slug,
            content=content,
            message_type="dialogue",
            task_id=task_id,
        )

        if "error" in result:
            return result

        result["guidance"] = (
            "Question posted. You should now:\n"
            "1. Wait for an answer before proceeding with related work\n"
            "2. Check roboco_channel_history periodically for responses\n"
            "3. If urgent, consider mentioning the PM"
        )

        return result

    # =========================================================================
    # REPORT BLOCKER (convenience wrapper)
    # =========================================================================

    @mcp.tool()
    async def roboco_report_blocker(
        channel_slug: str,
        blocker_description: str,
        what_needed: str,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Report a blocker in a channel (convenience wrapper).

        This automatically formats the message as a blocker report
        and notifies the PM.

        Args:
            channel_slug: The channel to report in
            blocker_description: What is blocking you
            what_needed: What is needed to unblock
            task_id: Optional task this relates to

        Returns:
            Sent blocker message
        """
        content = (
            f"**BLOCKER**\n\n"
            f"**Issue**: {blocker_description}\n\n"
            f"**Needed to unblock**: {what_needed}"
        )

        result = await roboco_message_send(
            channel_slug=channel_slug,
            content=content,
            message_type="blocker",
            task_id=task_id,
        )

        if "error" in result:
            return result

        result["guidance"] = (
            "Blocker reported. The PM will be notified.\n"
            "You should:\n"
            "1. Wait for resolution, or\n"
            "2. Switch to another task (call roboco_task_scan)"
        )

        return result

    return mcp


# =============================================================================
# STANDALONE RUNNER
# =============================================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("Usage: python message_server.py <agent_id>")
        sys.exit(1)

    agent_id = sys.argv[1]
    server = create_message_mcp_server(agent_id)
    server.run()
