"""
Task Scan Handlers

Handlers for scanning and getting tasks.
"""

from typing import Any

from fastapi import status

from roboco.agents_config import get_agent_role
from roboco.mcp.tasks import format_task_response, get_next_step_guidance
from roboco.mcp.tasks.handlers._helpers import (
    get_available_tasks_for_role,
    get_scan_guidance,
)
from roboco.mcp.utils import ApiClient, format_error_response


async def handle_task_scan(
    client: ApiClient, team: str | None, agent_id: str
) -> dict[str, Any]:
    """Handle task scanning."""
    paused_resp = await client.get("/tasks/my", params={"status": "paused"})
    paused_tasks = paused_resp.json() if paused_resp.ok else []

    assigned_resp = await client.get("/tasks/my")
    assigned_data = assigned_resp.json() if assigned_resp.ok else []
    active_statuses = {
        "pending",
        "claimed",
        "in_progress",
        "verifying",
        "needs_revision",
    }
    assigned_tasks = [t for t in assigned_data if t.get("status") in active_statuses]

    agent_role = get_agent_role(agent_id)
    available_tasks = await get_available_tasks_for_role(client, agent_role, team)

    assigned_ids = {t.get("id") for t in assigned_tasks}
    available_tasks = [t for t in available_tasks if t.get("id") not in assigned_ids]

    return {
        "paused_tasks": paused_tasks,
        "assigned_tasks": assigned_tasks,
        "available_tasks": available_tasks,
        "guidance": get_scan_guidance(
            paused_tasks, assigned_tasks, available_tasks, agent_role
        ),
    }


async def handle_task_get(client: ApiClient, task_id: str) -> dict[str, Any]:
    """Handle getting task details."""
    resp = await client.get(f"/tasks/{task_id}")

    if resp.is_status(status.HTTP_404_NOT_FOUND):
        return format_error_response("NOT_FOUND", f"Task {task_id} not found")

    task = resp.json()
    next_step, guidance = get_next_step_guidance(task.get("status", ""))
    return format_task_response(task, next_step, guidance)
