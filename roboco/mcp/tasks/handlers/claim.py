"""
Task Claim Handler

Handler for claiming tasks.
"""

from typing import Any

from roboco.agents_config import get_agent_role
from roboco.mcp.tasks import format_task_response
from roboco.mcp.tasks.handlers._helpers import (
    check_blocking_tasks,
    fetch_task_or_error,
    get_project_context,
    validate_task_claimable,
)
from roboco.mcp.utils import ApiClient, format_error_response


async def _check_active_tasks(client: ApiClient) -> dict[str, Any] | None:
    """Check for blocking tasks. Returns error or None.

    Note: Paused tasks no longer block claiming. Agents can verify why
    a task is paused (via roboco_task_scan) and decide to resume it
    or claim new work if it's legitimately waiting on something.
    """
    active_resp = await client.get("/tasks/my")
    if not active_resp.ok:
        return None
    active_tasks = active_resp.json()
    # Only block on in_progress tasks, not paused ones
    return check_blocking_tasks(active_tasks)


async def _execute_claim(
    client: ApiClient, task_id: str, agent_id: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Execute the claim API call. Returns (task, None) or (None, error)."""
    claim_resp = await client.post(
        f"/tasks/{task_id}/claim",
        json={"agent_id": agent_id},
    )
    if not claim_resp.ok:
        return None, format_error_response(
            "CLAIM_FAILED", "Failed to claim task", {"api_error": claim_resp.text}
        )
    claimed: dict[str, Any] = claim_resp.json()
    return claimed, None


async def handle_task_claim(
    client: ApiClient, task_id: str, agent_id: str
) -> dict[str, Any]:
    """Handle task claiming."""
    if error := await _check_active_tasks(client):
        return error

    task, error = await fetch_task_or_error(client, task_id)
    if error:
        return error
    assert task is not None

    agent_role = get_agent_role(agent_id)
    if error := await validate_task_claimable(task, agent_role, agent_id, client):
        return error

    claimed_task, error = await _execute_claim(client, task_id, agent_id)
    if error:
        return error
    assert claimed_task is not None

    project = None
    if claimed_task.get("project_id"):
        project = await get_project_context(client, claimed_task["project_id"])

    return format_task_response(
        claimed_task,
        "PLAN",
        "Task claimed. NEXT: Call roboco_task_plan() before you can start.\n"
        "1. Read the description and acceptance criteria\n"
        "2. Ask questions if anything is unclear\n"
        "3. Call roboco_task_plan(task_id, approach, steps)\n"
        "4. Then call roboco_task_start(task_id)",
        project=project,
    )
