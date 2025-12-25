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
    resolve_agent_uuid_cached,
    validate_task_claimable,
)
from roboco.mcp.utils import ApiClient, format_error_response


async def _check_active_tasks(
    client: ApiClient, exclude_task_id: str | None = None
) -> dict[str, Any] | None:
    """Check for blocking tasks. Returns error or None.

    Args:
        client: API client
        exclude_task_id: Task ID to exclude from blocking check. Used when
            the agent is claiming a task already assigned to them.
    """
    active_resp = await client.get("/tasks/my")
    if not active_resp.ok:
        return None
    active_tasks = active_resp.json()

    # Exclude the task being claimed if specified
    if exclude_task_id:
        active_tasks = [t for t in active_tasks if str(t.get("id")) != exclude_task_id]

    return check_blocking_tasks(active_tasks)


async def _is_pre_assigned_to_agent(
    task: dict[str, Any], agent_id: str, client: ApiClient
) -> bool:
    """Check if task is pre-assigned to this agent (PM assigned before claim)."""
    assigned_to = task.get("assigned_to")
    if not assigned_to:
        return False

    # Task must be pending (not yet claimed)
    if task.get("status") != "pending":
        return False

    agent_uuid = await resolve_agent_uuid_cached(agent_id, client)
    return agent_uuid is not None and str(assigned_to) == agent_uuid


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
    """Handle task claiming.

    Flow:
    1. Fetch the task first
    2. Check if it's pre-assigned to this agent (PM assigned directly)
    3. If pre-assigned, skip blocking check for THIS task
    4. Otherwise, run full blocking check
    5. Validate task is claimable for this role
    6. Execute claim
    """
    # Fetch task first - we need to check if it's pre-assigned
    task, error = await fetch_task_or_error(client, task_id)
    if error:
        return error
    assert task is not None

    # Check if this task is pre-assigned to the agent
    is_pre_assigned = await _is_pre_assigned_to_agent(task, agent_id, client)

    # Check for blocking tasks (exclude this task if pre-assigned)
    exclude_id = task_id if is_pre_assigned else None
    if error := await _check_active_tasks(client, exclude_task_id=exclude_id):
        return error

    # Validate task can be claimed by this role
    agent_role = get_agent_role(agent_id)
    if error := await validate_task_claimable(task, agent_role, agent_id, client):
        return error

    # Execute the claim
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
