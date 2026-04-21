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

_ACTIVE_SCAN_STATUSES: frozenset[str] = frozenset(
    {"pending", "claimed", "in_progress", "verifying", "needs_revision"}
)

_PM_SCAN_ROLES: frozenset[str] = frozenset(
    {"cell_pm", "main_pm", "product_owner", "auditor", "ceo"}
)


async def _scan_assigned_active(client: ApiClient) -> list[dict[str, Any]]:
    """Return this agent's assigned tasks that are still active."""
    assigned_resp = await client.get("/tasks/my")
    assigned_data = assigned_resp.json() if assigned_resp.ok else []
    return [t for t in assigned_data if t.get("status") in _ACTIVE_SCAN_STATUSES]


async def _scan_blocked_for_pm(
    client: ApiClient, agent_role: str | None, team: str | None
) -> list[dict[str, Any]]:
    """Return blocked tasks for PMs/board; empty list for other roles."""
    if agent_role not in _PM_SCAN_ROLES:
        return []
    params: dict[str, str] = {}
    if team:
        params["team"] = team
    blocked_resp = await client.get("/tasks/blocked", params=params)
    return blocked_resp.json() if blocked_resp.ok else []


async def handle_task_scan(
    client: ApiClient, team: str | None, agent_id: str
) -> dict[str, Any]:
    """Handle task scanning."""
    paused_resp = await client.get("/tasks/my", params={"status": "paused"})
    paused_tasks = paused_resp.json() if paused_resp.ok else []

    assigned_tasks = await _scan_assigned_active(client)

    agent_role = get_agent_role(agent_id)
    available_tasks = await get_available_tasks_for_role(client, agent_role, team)

    assigned_ids = {t.get("id") for t in assigned_tasks}
    available_tasks = [t for t in available_tasks if t.get("id") not in assigned_ids]

    blocked_tasks = await _scan_blocked_for_pm(client, agent_role, team)

    result: dict[str, Any] = {
        "paused_tasks": paused_tasks,
        "assigned_tasks": assigned_tasks,
        "available_tasks": available_tasks,
        "guidance": get_scan_guidance(
            paused_tasks, assigned_tasks, available_tasks, agent_role
        ),
    }

    if blocked_tasks:
        result["blocked_tasks"] = blocked_tasks
        result["blocked_action_required"] = (
            f"⚠️ {len(blocked_tasks)} BLOCKED task(s) need your attention!\n"
            "For each resolved blocker, you MUST call:\n"
            "  roboco_task_unblock(task_id)\n\n"
            "Verbal resolution in chat is NOT enough."
        )

    return result


async def handle_task_get(client: ApiClient, task_id: str) -> dict[str, Any]:
    """Handle getting task details."""
    resp = await client.get(f"/tasks/{task_id}")

    if resp.is_status(status.HTTP_404_NOT_FOUND):
        return format_error_response("NOT_FOUND", f"Task {task_id} not found")

    task = resp.json()
    next_step, guidance = get_next_step_guidance(task.get("status", ""))
    return format_task_response(task, next_step, guidance)
