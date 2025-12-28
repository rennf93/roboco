"""
A2A MCP Server

Provides tools for agent-to-agent communication using the A2A protocol.
This enables peer-to-peer agent collaboration without going through
the orchestrator for every interaction.

Tools available to ALL agents:
- roboco_agent_discover: Discover other agents by skill/role/team
- roboco_agent_request: Request another agent to perform work
- roboco_agent_request_status: Check status of a pending request
"""

from typing import Any

from mcp.server.fastmcp import FastMCP

from roboco.agents_config import (
    ALL_AGENTS,
    get_agent_role,
    get_agent_skills,
    get_agent_team,
)
from roboco.mcp.utils import ApiClient, format_error_response
from roboco.seeds.initial_data import AGENT_UUIDS


# =============================================================================
# TOOL IMPLEMENTATIONS
# =============================================================================


async def _handle_discover(
    client: ApiClient,
    role: str | None = None,
    team: str | None = None,
    skill: str | None = None,
) -> dict[str, Any]:
    """Discover agents by criteria."""
    # Build local discovery (fast path - no API call needed)
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
            f"Found {len(agents)} agent(s). Use roboco_agent_request to request "
            "work from a specific agent."
        ),
    }


async def _handle_request(
    client: ApiClient,
    agent_id: str,
    target_agent: str,
    skill: str,
    message: str,
    task_id: str | None = None,
    blocking: bool = False,
) -> dict[str, Any]:
    """Request another agent to perform work via A2A."""
    # Validate target agent exists
    if target_agent not in ALL_AGENTS:
        return format_error_response(
            "AGENT_NOT_FOUND",
            f"Agent '{target_agent}' not found. Use roboco_agent_discover to find agents.",
        )

    # Validate skill exists for target
    target_skills = get_agent_skills(target_agent)
    skill_ids = [s.get("id", "") for s in target_skills]
    if skill not in skill_ids:
        return format_error_response(
            "SKILL_NOT_FOUND",
            f"Agent '{target_agent}' does not have skill '{skill}'. "
            f"Available skills: {', '.join(skill_ids)}",
        )

    # Resolve target agent UUID
    target_uuid = AGENT_UUIDS.get(target_agent)
    if not target_uuid:
        return format_error_response(
            "AGENT_UUID_NOT_FOUND",
            f"Could not resolve UUID for agent '{target_agent}'",
        )

    # Build A2A message payload
    payload = {
        "message": {
            "role": "user",
            "parts": [{"type": "text", "text": message}],
            "contextId": task_id or f"request-{agent_id}-to-{target_agent}",
        },
        "configuration": {
            "blocking": blocking,
            "acceptedOutputModes": ["text/plain", "application/json"],
        },
        "metadata": {
            "from_agent": agent_id,
            "target_agent": target_agent,
            "skill": skill,
            "task_id": task_id,
        },
    }

    # Send A2A request
    resp = await client.post("/a2a/message/send", json=payload)

    if not resp.ok:
        return format_error_response(
            "A2A_REQUEST_FAILED",
            f"Failed to send A2A request: {resp.text}",
        )

    result = resp.json()
    a2a_task = result.get("task", {})
    a2a_task_id = a2a_task.get("id", "unknown")
    status = a2a_task.get("status", {}).get("state", "submitted")

    return {
        "status": "submitted",
        "a2a_task_id": a2a_task_id,
        "target_agent": target_agent,
        "skill": skill,
        "state": status,
        "guidance": (
            f"Request sent to {target_agent}. "
            f"Task ID: {a2a_task_id}. "
            "Use roboco_agent_request_status to check progress, or wait for "
            "a notification when complete."
        ),
    }


async def _handle_request_status(
    client: ApiClient,
    a2a_task_id: str,
) -> dict[str, Any]:
    """Check status of an A2A request."""
    resp = await client.get(f"/a2a/tasks/{a2a_task_id}")

    if resp.is_status(404):
        return format_error_response(
            "TASK_NOT_FOUND",
            f"A2A task '{a2a_task_id}' not found",
        )

    if not resp.ok:
        return format_error_response(
            "STATUS_CHECK_FAILED",
            f"Failed to check status: {resp.text}",
        )

    task = resp.json()
    status = task.get("status", {})
    state = status.get("state", "unknown")
    message = status.get("message", {})

    result_text = None
    if message and message.get("parts"):
        for part in message["parts"]:
            if part.get("type") == "text":
                result_text = part.get("text")
                break

    guidance = ""
    if state == "completed":
        guidance = "Request completed. Review the result below."
    elif state == "working":
        guidance = "Agent is still working on this request. Check again later."
    elif state == "input_required":
        guidance = "Agent needs more information. Review the message and respond."
    elif state in ["failed", "cancelled", "rejected"]:
        guidance = f"Request ended with state: {state}."

    return {
        "a2a_task_id": a2a_task_id,
        "state": state,
        "result": result_text,
        "artifacts": task.get("artifacts", []),
        "metadata": task.get("metadata", {}),
        "guidance": guidance,
    }


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
    client = ApiClient(agent_id)

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
        return await _handle_discover(client, role, team, skill)

    @mcp.tool()
    async def roboco_agent_request(
        target_agent: str,
        skill: str,
        message: str,
        task_id: str | None = None,
        blocking: bool = False,
    ) -> dict[str, Any]:
        """
        Request another agent to perform work using A2A protocol.

        This enables direct peer-to-peer collaboration between agents.

        Args:
            target_agent: Agent slug to request (e.g., "be-qa", "fe-dev-1")
            skill: Skill to invoke (e.g., "code_review", "code_implementation")
            message: Description of what you need
            task_id: Related task ID (optional, for context)
            blocking: Wait for response (default: false, async)

        Returns:
            A2A task ID for tracking the request
        """
        return await _handle_request(
            client, agent_id, target_agent, skill, message, task_id, blocking
        )

    @mcp.tool()
    async def roboco_agent_request_status(
        a2a_task_id: str,
    ) -> dict[str, Any]:
        """
        Check the status of an A2A request.

        Args:
            a2a_task_id: The A2A task ID returned from roboco_agent_request

        Returns:
            Current status and any results
        """
        return await _handle_request_status(client, a2a_task_id)

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
