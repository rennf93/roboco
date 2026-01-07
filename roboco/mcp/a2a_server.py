"""
A2A MCP Server

Provides tools for agent-to-agent communication.
Routes messages through the local SDK Server for true peer-to-peer A2A.

Tools available to ALL agents:
- roboco_agent_discover: Discover other agents by skill/role/team
- roboco_agent_request: Send A2A message to another agent (via SDK)
- roboco_a2a_check: Poll inbox for incoming A2A messages
"""

import contextlib
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

from roboco.agents_config import (
    ALL_AGENTS,
    can_a2a_direct,
    get_a2a_route_hint,
    get_agent_role,
    get_agent_skills,
    get_agent_team,
)
from roboco.mcp.utils import format_error_response

# Current agent ID from environment (set by orchestrator)
AGENT_ID = os.environ.get("ROBOCO_AGENT_ID", "unknown")

# SDK Server configuration
SDK_URL = os.environ.get("ROBOCO_SDK_URL", "http://localhost:9000")

# Main API URL (for notification auto-ack)
API_URL = os.environ.get("ROBOCO_API_URL", "http://localhost:8000")


# =============================================================================
# TOOL IMPLEMENTATIONS
# =============================================================================


async def _handle_discover(
    role: str | None = None,
    team: str | None = None,
    skill: str | None = None,
) -> dict[str, Any]:
    """Discover agents by criteria (local lookup, no API call)."""
    agents = []

    for agent_slug in ALL_AGENTS:
        agent_role = get_agent_role(agent_slug)
        agent_team = get_agent_team(agent_slug)
        agent_skills = get_agent_skills(agent_slug)

        # Apply filters
        if role and agent_role != role:
            continue
        if team and agent_team != team:
            continue
        if skill:
            skill_ids = [s.get("id", "") for s in agent_skills]
            skill_tags = []
            for s in agent_skills:
                skill_tags.extend(s.get("tags", []))
            if skill not in skill_ids and skill not in skill_tags:
                continue

        agents.append(
            {
                "slug": agent_slug,
                "role": agent_role,
                "team": agent_team,
                "skills": [
                    {"id": s["id"], "name": s["name"], "description": s["description"]}
                    for s in agent_skills
                ],
            }
        )

    return {
        "agents": agents,
        "count": len(agents),
        "guidance": (
            f"Found {len(agents)} agent(s). Use roboco_agent_request to send "
            "a message to a specific agent."
        ),
    }


def _validate_a2a_target(
    from_agent: str, target_agent: str, skill: str
) -> dict[str, Any] | None:
    """Validate A2A target and permissions. Returns error dict or None if valid."""
    # Check target exists
    if target_agent not in ALL_AGENTS:
        return format_error_response(
            "AGENT_NOT_FOUND",
            f"Agent '{target_agent}' not found. Use roboco_agent_discover.",
        )

    # Check A2A hierarchy permissions
    allowed, error_msg = can_a2a_direct(from_agent, target_agent)
    if not allowed:
        return format_error_response(
            "A2A_NOT_PERMITTED",
            error_msg or f"Cannot A2A {target_agent} directly.",
            hint=get_a2a_route_hint(from_agent, target_agent),
        )

    # Check skill exists
    target_skills = get_agent_skills(target_agent)
    skill_ids = [s.get("id", "") for s in target_skills]
    if skill not in skill_ids:
        available = ", ".join(skill_ids)
        return format_error_response(
            "SKILL_NOT_FOUND",
            f"Agent '{target_agent}' lacks skill '{skill}'. Has: {available}",
        )

    return None


async def _auto_ack_a2a_notifications(
    from_agent: str, target_agent: str, task_id: str
) -> None:
    """Auto-acknowledge A2A notifications when responding.

    When agent B responds to agent A about a task, ack any pending
    A2A_REQUEST notifications from A about that task.
    """
    async with httpx.AsyncClient() as client:
        with contextlib.suppress(Exception):
            await client.post(
                f"{API_URL}/api/v1/notifications/ack-a2a",
                json={
                    "from_agent": target_agent,  # Original sender
                    "to_agent": from_agent,  # Us (the responder)
                    "task_id": task_id,
                },
                timeout=5.0,
            )


async def _check_pending_a2a(
    from_agent: str, target_agent: str, task_id: str
) -> dict[str, Any] | None:
    """Check if there's already a pending A2A to target about this task.

    Returns error dict if pending message exists, None if ok to send.
    """
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(
                f"{API_URL}/api/v1/notifications/pending-a2a",
                params={
                    "from_agent": from_agent,
                    "to_agent": target_agent,
                    "task_id": task_id,
                },
                timeout=5.0,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("has_pending"):
                return format_error_response(
                    "A2A_PENDING",
                    f"Already sent A2A to {target_agent} about this task.",
                    hint="Wait for their response before sending another message.",
                )
        except Exception:
            pass  # Non-critical check, allow send if check fails
    return None


async def _send_via_sdk(
    target_agent: str, skill: str, message: str, task_id: str, urgent: bool
) -> dict[str, Any]:
    """Send A2A message via SDK Server."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(
                f"{SDK_URL}/a2a/send",
                json={
                    "target_agent": target_agent,
                    "skill": skill,
                    "message": message,
                    "task_id": task_id,
                    "urgent": urgent,
                },
                timeout=10.0,
            )
            resp.raise_for_status()
            result = resp.json()

            delivery = result.get("delivery", "unknown")
            urgency_note = " (URGENT)" if urgent else ""

            return {
                "status": "success",
                "target_agent": target_agent,
                "skill": skill,
                "task_id": task_id,
                "message_id": result.get("message_id"),
                "delivery": delivery,
                "guidance": (
                    f"A2A sent to {target_agent}{urgency_note}. Delivery: {delivery}."
                ),
            }

        except httpx.ConnectError:
            return format_error_response(
                "SDK_UNAVAILABLE",
                "SDK Server not available.",
                hint="SDK Server should be running alongside Claude Code.",
            )
        except httpx.HTTPStatusError as e:
            return format_error_response(
                "A2A_SEND_FAILED",
                f"Failed to send: {e.response.text}",
            )


async def _handle_check() -> dict[str, Any]:
    """Poll inbox for incoming A2A messages via SDK Server."""
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(f"{SDK_URL}/inbox/poll", timeout=5.0)
            resp.raise_for_status()
            data = resp.json()

            messages = data.get("messages", [])
            count = data.get("count", 0)

            if count == 0:
                return {
                    "messages": [],
                    "count": 0,
                    "guidance": "No pending A2A messages.",
                }

            # Format messages for display
            formatted = []
            for msg in messages:
                formatted.append(
                    {
                        "id": str(msg.get("id", "")),
                        "from": msg.get("from_agent", "unknown"),
                        "task_id": msg.get("task_id", ""),
                        "skill": msg.get("skill", ""),
                        "message": msg.get("content", ""),
                        "priority": msg.get("priority", "normal"),
                        "timestamp": msg.get("timestamp", ""),
                    }
                )

            return {
                "messages": formatted,
                "count": count,
                "guidance": (
                    f"You have {count} A2A message(s). "
                    "Review and respond to each as appropriate."
                ),
            }

        except httpx.ConnectError:
            return format_error_response(
                "SDK_UNAVAILABLE",
                "SDK Server is not available. Cannot check inbox.",
                hint="The SDK Server should be running alongside Claude Code.",
            )
        except httpx.HTTPStatusError as e:
            return format_error_response(
                "INBOX_CHECK_FAILED",
                f"Failed to check inbox: {e.response.text}",
            )


# =============================================================================
# MCP SERVER FACTORY
# =============================================================================


def create_a2a_mcp_server(agent_id: str) -> FastMCP:
    """
    Create an A2A MCP server for a specific agent.

    Args:
        agent_id: The agent identifier (e.g., "be-dev-1")

    Returns:
        Configured FastMCP server
    """
    mcp = FastMCP(f"roboco-a2a-{agent_id}", json_response=True)

    @mcp.tool()
    async def roboco_agent_discover(
        role: str | None = None,
        team: str | None = None,
        skill: str | None = None,
    ) -> dict[str, Any]:
        """
        Discover other agents by role, team, or skill.

        Use this to find which agents can help with specific tasks.

        Args:
            role: Filter by role (developer, qa, documenter, cell_pm, main_pm)
            team: Filter by team (backend, frontend, ux_ui)
            skill: Filter by skill ID or tag (code_review, testing, etc.)

        Returns:
            List of matching agents with their capabilities
        """
        return await _handle_discover(role, team, skill)

    @mcp.tool()
    async def roboco_agent_request(
        target_agent: str,
        skill: str,
        message: str,
        task_id: str,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Send an A2A message to another agent about a specific task.

        Messages are delivered directly if the agent is online,
        or via notification if they are offline.

        Args:
            target_agent: Agent slug to message (e.g., "be-qa", "fe-dev-1")
            skill: Skill being requested (e.g., "code_review", "clarification")
            message: Your message content
            task_id: REQUIRED - The task this message is about
            options: Optional dict with 'urgent' (bool) flag

        Returns:
            Status of the A2A message delivery
        """
        if not task_id:
            return format_error_response(
                "TASK_ID_REQUIRED",
                "A2A messages must reference a task. Provide task_id.",
                hint="A2A is for communication about existing tasks.",
            )

        # Validate permissions (hierarchy enforcement)
        validation_error = _validate_a2a_target(agent_id, target_agent, skill)
        if validation_error:
            return validation_error

        # Check if we already have a pending A2A to this agent about this task
        pending_error = await _check_pending_a2a(agent_id, target_agent, task_id)
        if pending_error:
            return pending_error

        opts = options or {}
        urgent = opts.get("urgent", False)

        # Auto-ack any pending A2A notifications from target about this task
        # (responding = acknowledging the original request)
        await _auto_ack_a2a_notifications(agent_id, target_agent, task_id)

        return await _send_via_sdk(
            target_agent=target_agent,
            skill=skill,
            message=message,
            task_id=task_id,
            urgent=urgent,
        )

    @mcp.tool()
    async def roboco_a2a_check() -> dict[str, Any]:
        """
        Check for incoming A2A messages.

        Poll your inbox for messages from other agents.
        Messages are removed from the queue once retrieved.

        Returns:
            List of pending A2A messages
        """
        return await _handle_check()

    return mcp


# =============================================================================
# STANDALONE RUNNER
# =============================================================================

if __name__ == "__main__":
    import sys

    MIN_ARGS = 2
    if len(sys.argv) < MIN_ARGS:
        print("Usage: python a2a_server.py <agent_id>")
        sys.exit(1)

    agent_id_arg = sys.argv[1]
    server = create_a2a_mcp_server(agent_id_arg)
    server.run()
