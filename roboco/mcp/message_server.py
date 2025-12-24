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

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import status
from mcp.server.fastmcp import FastMCP

from roboco.agents_config import CHANNEL_ACCESS, get_agent_role
from roboco.llm import ToonAdapter
from roboco.mcp.schemas import (
    AskQuestionInput,
    ReportBlockerInput,
    SendMessageInput,
)
from roboco.mcp.utils import (
    ApiClient,
    format_error_response,
    resolve_agent_uuid_cached,
)

# Global TOON adapter for encoding message data
_toon = ToonAdapter()


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def _format_no_groups_error(agent_id: str, channel_slug: str) -> dict[str, Any]:
    """Format NO_GROUPS error with role-aware escalation guidance."""
    role = get_agent_role(agent_id)

    if role == "main_pm":
        guidance = (
            f"Channel #{channel_slug} has no groups. "
            "Use roboco_group_create to create a group for this channel."
        )
    elif role == "cell_pm":
        guidance = (
            f"Channel #{channel_slug} has no groups. "
            "Groups are created by Main PM. "
            "Use roboco_task_escalate to request group creation."
        )
    else:
        # Developer/QA/Documenter
        guidance = (
            f"Channel #{channel_slug} has no groups yet. "
            "Escalate to your Cell PM. "
            "If you have a task_id, include it in your message call."
        )

    return format_error_response(
        "NO_GROUPS",
        f"Channel #{channel_slug} has no groups.",
        {"guidance": guidance, "channel": channel_slug, "role": role},
    )


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
        return format_error_response(
            "INVALID_TYPE",
            f"Invalid message type '{message_type}'. Must be one of: {valid_types}",
        )

    if not _check_channel_access(agent_id, channel_slug, "write"):
        writable = [
            ch for ch in CHANNEL_ACCESS if _check_channel_access(agent_id, ch, "write")
        ]
        return format_error_response(
            "ACCESS_DENIED",
            f"You don't have write access to #{channel_slug}",
            {"your_writable_channels": writable},
        )

    if agent_id in CHANNEL_ACCESS.get(channel_slug, {}).get("silent", []):
        return format_error_response(
            "SILENT_OBSERVER",
            "You are a silent observer on this channel and cannot post messages.",
        )

    if not content or not content.strip():
        return format_error_response(
            "EMPTY_CONTENT", "Message content cannot be empty."
        )

    return None


async def _get_default_group(
    client: ApiClient,
    channel_id: str,
    agent_id: str | None = None,
    channel_slug: str | None = None,
) -> str | dict[str, Any]:
    """Get the default (first) group for a channel. Returns group_id or error dict.

    If agent_id and channel_slug are provided, NO_GROUPS error includes
    role-aware escalation guidance.
    """
    resp = await client.get(f"/channels/{channel_id}/groups")

    if not resp.ok:
        return format_error_response(
            "GROUPS_ERROR",
            "Failed to get channel groups",
            {"status": resp.status_code},
        )

    groups = resp.json()
    if not groups:
        # Return role-aware guidance if we have context
        if agent_id and channel_slug:
            return _format_no_groups_error(agent_id, channel_slug)
        return format_error_response("NO_GROUPS", "Channel has no groups")

    # Return first active group, or first group if none are active
    for group in groups:
        if group.get("is_active", True):
            return str(group["id"])
    return str(groups[0]["id"])


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


async def _get_channel_by_slug(
    client: ApiClient,
    channel_slug: str,
) -> str | dict[str, Any]:
    """Get channel ID by slug. Returns channel_id or error dict."""
    resp = await client.get("/channels", params={"slug": channel_slug})

    if not resp.ok:
        return format_error_response("API_ERROR", "Failed to fetch channels")

    data = resp.json()
    items = data.get("items", data)
    if not items:
        return format_error_response("NOT_FOUND", f"Channel #{channel_slug} not found")

    channel = items[0] if isinstance(items, list) else items
    return str(channel["id"])


async def _get_sessions_for_group(
    client: ApiClient,
    group_id: str,
) -> list | dict[str, Any]:
    """Get sessions for a group. Returns session list or error dict."""
    resp = await client.get("/sessions", params={"group_id": group_id, "limit": 5})

    if not resp.ok:
        return format_error_response("API_ERROR", "Failed to fetch sessions")

    items: list = resp.json().get("items", [])
    return items


async def _fetch_messages_from_sessions(
    client: ApiClient,
    sessions: list,
    since: datetime,
    limit: int,
) -> list:
    """Fetch messages from multiple sessions."""
    all_messages: list = []
    for session in sessions:
        resp = await client.get(
            "/messages",
            params={
                "session_id": session["id"],
                "after": since.isoformat(),
                "limit": limit,
            },
        )
        if resp.ok:
            all_messages.extend(resp.json().get("items", []))
        if len(all_messages) >= limit:
            break

    all_messages.sort(key=lambda m: m.get("timestamp", ""), reverse=True)
    return all_messages[:limit]


async def _handle_channel_history(
    client: ApiClient,
    agent_id: str,
    channel_slug: str,
    limit: int,
    hours_back: int,
) -> dict[str, Any]:
    """Handle channel history retrieval."""
    if not _check_channel_access(agent_id, channel_slug, "read"):
        return format_error_response(
            "ACCESS_DENIED", f"You don't have read access to #{channel_slug}"
        )

    max_limit = 100
    limit = min(limit, max_limit)
    since = datetime.now(UTC) - timedelta(hours=hours_back)

    # Get channel
    channel_result = await _get_channel_by_slug(client, channel_slug)
    if isinstance(channel_result, dict):
        return channel_result
    channel_id = channel_result

    # Get group (with role-aware guidance if NO_GROUPS)
    group_result = await _get_default_group(
        client, channel_id, agent_id=agent_id, channel_slug=channel_slug
    )
    if isinstance(group_result, dict):
        return group_result
    group_id = group_result

    # Get sessions
    sessions_result = await _get_sessions_for_group(client, group_id)
    if isinstance(sessions_result, dict):
        return sessions_result
    sessions = sessions_result

    # Early return for no sessions
    if not sessions:
        return {
            "channel": channel_slug,
            "messages": [],
            "total": 0,
            "has_more": False,
            "since": since.isoformat(),
        }

    # Fetch messages
    messages = await _fetch_messages_from_sessions(client, sessions, since, limit)

    return {
        "channel": channel_slug,
        "messages": messages,
        "total": len(messages),
        "has_more": len(messages) >= limit,
        "since": since.isoformat(),
    }


async def _get_task_primary_session(
    client: ApiClient, task_id: str, max_depth: int = 5
) -> str | None:
    """Get the primary session ID for a task.

    If the task has no session, traverses up the parent hierarchy
    to find the parent's session. Subtasks inherit their parent's session.

    Args:
        client: API client
        task_id: The task to find session for
        max_depth: Maximum parent levels to traverse (prevents infinite loops)

    Returns:
        Session ID or None if no session found in hierarchy
    """
    current_task_id = task_id
    depth = 0

    while current_task_id and depth < max_depth:
        # Check if this task has a session
        resp = await client.get(f"/sessions/for-task/{current_task_id}")
        if resp.ok:
            sessions = resp.json()
            if sessions:
                # Find primary session
                for session in sessions:
                    if session.get("is_primary"):
                        return str(session.get("session_id"))
                # Fall back to first session if no primary marked
                return str(sessions[0].get("session_id"))

        # No session found - check if this is a subtask with a parent
        task_resp = await client.get(f"/tasks/{current_task_id}")
        if not task_resp.ok:
            return None

        task_data = task_resp.json()
        parent_id = task_data.get("parent_task_id")

        if not parent_id:
            # No parent - we've reached the top without finding a session
            return None

        # Traverse up to parent
        current_task_id = parent_id
        depth += 1

    return None


async def _handle_message_send(
    client: ApiClient,
    agent_id: str,
    data: SendMessageInput,
) -> dict[str, Any]:
    """Handle message sending.

    If task_id is provided, routes to that task's primary session.
    Otherwise, routes to the channel's current active session.
    """
    if validation_error := _validate_message_send(
        agent_id, data.channel_slug, data.content, data.message_type
    ):
        return validation_error

    # task_id is required - use task's linked session (or parent's session for subtasks)
    session_id = await _get_task_primary_session(client, data.task_id)
    if not session_id:
        # Task has no linked session and no parent with session - PM setup issue
        return format_error_response(
            "NO_TASK_SESSION",
            f"Task {data.task_id} has no linked session (checked parent hierarchy).",
            {
                "guidance": (
                    "Neither this task nor its parent have a work session.\n"
                    "Cell PM must create one with roboco_session_create_for_tasks\n"
                    "for the PARENT task before subtasks can be worked on.\n"
                    "Escalate to your PM using roboco_task_escalate."
                ),
                "task_id": data.task_id,
            },
        )

    # Resolve mentions (slugs) to UUIDs using shared cache
    resolved_mentions: list[str] = []
    if data.mentions:
        for mention in data.mentions:
            resolved = await resolve_agent_uuid_cached(mention, client)
            if resolved:
                resolved_mentions.append(resolved)
            # Skip unresolved mentions rather than failing

    message_data = {
        "session_id": session_id,
        "type": data.message_type,
        "content": data.content,
        "is_reply": data.reply_to is not None,
        "reply_to": data.reply_to,
        "mentions": resolved_mentions,
        "task_id": data.task_id,
    }

    resp = await client.post("/messages", json=message_data)

    if not resp.ok:
        return format_error_response(
            "SEND_FAILED", "Failed to send message", {"api_error": resp.text}
        )

    return {
        "status": "sent",
        "message": resp.json(),
        "channel": data.channel_slug,
        "task_id": data.task_id,
        "guidance": f"Message sent to task {data.task_id}'s session.",
    }


async def _handle_message_get(client: ApiClient, message_id: str) -> dict[str, Any]:
    """Handle message retrieval."""
    resp = await client.get(f"/messages/{message_id}")

    if resp.is_status(status.HTTP_404_NOT_FOUND):
        return format_error_response("NOT_FOUND", f"Message {message_id} not found")

    if not resp.ok:
        return format_error_response("API_ERROR", "Failed to fetch message")

    return {"message": resp.json()}


async def _handle_ask_question(
    client: ApiClient,
    agent_id: str,
    data: AskQuestionInput,
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
    result = await _handle_message_send(client, agent_id, msg_data)

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
    client: ApiClient,
    agent_id: str,
    data: ReportBlockerInput,
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
    result = await _handle_message_send(client, agent_id, msg_data)

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

    # Create shared API client for this agent
    client = ApiClient(agent_id)

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
        return await _handle_channel_history(
            client, agent_id, channel_slug, limit, hours_back
        )

    @mcp.tool()
    async def roboco_message_send(data: SendMessageInput) -> dict[str, Any]:
        """
        Send a message to a channel.

        You must have write access to the channel.
        """
        return await _handle_message_send(client, agent_id, data)

    @mcp.tool()
    async def roboco_message_get(message_id: str) -> dict[str, Any]:
        """Get a specific message by ID."""
        return await _handle_message_get(client, message_id)

    @mcp.tool()
    async def roboco_ask_question(
        channel_slug: str,
        question: str,
        task_id: str,
        context: str | None = None,
    ) -> dict[str, Any]:
        """
        Ask a question in a channel.

        After asking, wait for an answer before proceeding.
        """
        data = AskQuestionInput(
            channel_slug=channel_slug,
            question=question,
            task_id=task_id,
            context=context,
        )
        return await _handle_ask_question(client, agent_id, data)

    @mcp.tool()
    async def roboco_report_blocker(
        channel_slug: str,
        blocker_description: str,
        what_needed: str,
        task_id: str,
    ) -> dict[str, Any]:
        """
        Report a blocker in a channel.

        The PM will be notified automatically.
        """
        data = ReportBlockerInput(
            channel_slug=channel_slug,
            blocker_description=blocker_description,
            what_needed=what_needed,
            task_id=task_id,
        )
        return await _handle_report_blocker(client, agent_id, data)

    @mcp.tool()
    async def roboco_session_history_for_task(
        task_id: str,
        limit: int = 50,
    ) -> dict[str, Any]:
        """
        Get message history from your task's work session.

        Use this to see the discussion context for a task you're working on.
        Returns messages from the task's primary session.

        Args:
            task_id: The task ID to get session history for
            limit: Maximum number of messages to return (default 50)
        """
        # Get task's primary session
        session_id = await _get_task_primary_session(client, task_id)
        if not session_id:
            return {
                "error": "NO_SESSION",
                "message": f"Task {task_id} has no linked session.",
                "guidance": (
                    "This task doesn't have a work session yet. "
                    "The PM should create one before work begins."
                ),
            }

        # Get messages from the session
        resp = await client.get(
            "/messages",
            params={"session_id": session_id, "limit": limit},
        )

        if not resp.ok:
            return format_error_response(
                "FETCH_FAILED",
                "Failed to fetch session history",
                {"api_error": resp.text},
            )

        messages = resp.json()
        return {
            "task_id": task_id,
            "session_id": session_id,
            "message_count": len(messages),
            "messages": messages,
            "guidance": (
                "This is the discussion history for your task. "
                "Use roboco_message_send with task_id to add to it."
            ),
        }

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
