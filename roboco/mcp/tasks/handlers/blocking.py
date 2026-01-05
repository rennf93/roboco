"""
Task Blocking Handlers

Handlers for blocking, unblocking, and pausing tasks.
"""

from typing import Any

from fastapi import status

from roboco.agents_config import get_agent_cell, get_agent_role
from roboco.mcp.schemas import TaskBlockInput, TaskPauseInput
from roboco.mcp.tasks import format_task_response
from roboco.mcp.tasks.handlers._helpers import validate_task_ownership
from roboco.mcp.utils import ApiClient, format_error_response


async def handle_task_block(
    client: ApiClient, data: TaskBlockInput, agent_id: str
) -> dict[str, Any]:
    """Handle task blocking via the soft-block endpoint."""
    task_resp = await client.get(f"/tasks/{data.task_id}")
    if task_resp.is_status(status.HTTP_404_NOT_FOUND):
        return format_error_response("NOT_FOUND", f"Task {data.task_id} not found")

    task = task_resp.json()

    if error := await validate_task_ownership(task, agent_id, client):
        return error

    if task.get("status") != "in_progress":
        return format_error_response(
            "INVALID_STATE", "Can only block in_progress tasks"
        )

    block_resp = await client.post(
        f"/tasks/{data.task_id}/soft-block",
        json={
            "reason": data.reason,
            "blocker_type": data.blocker_type,
            "what_needed": data.what_needed,
        },
    )

    if not block_resp.ok:
        return format_error_response(
            "BLOCK_FAILED",
            "Failed to block task",
            {"status_code": block_resp.status_code, "detail": block_resp.text},
        )

    # Determine PM agent based on agent's team
    team_prefix = agent_id[:2] if agent_id else "be"
    pm_agent = f"{team_prefix}-pm"

    return format_task_response(
        block_resp.json(),
        "RESOLVE_BLOCKER",
        f"Task blocked: {data.reason}\n\n"
        "✅ Your PM has been AUTOMATICALLY NOTIFIED with action required.\n"
        "   They must call roboco_task_unblock() when resolved.\n\n"
        "Your options:\n"
        "1. WAIT - PM will resolve and unblock\n"
        "2. SWITCH - Call roboco_task_scan for other work\n"
        "3. ESCALATE - Use roboco_task_escalate() if PM is unresponsive\n\n"
        "You'll be notified when the task is unblocked.",
        a2a_suggestion=(
            f"Need immediate PM attention? Use A2A:\n"
            f"roboco_agent_request('{pm_agent}', 'coordination', "
            f"'Blocked: {data.reason}', options={{'urgent': True}})"
        ),
    )


def _can_unblock_task(agent_id: str, task: dict) -> tuple[bool, str]:
    """Check if agent can unblock a task. PMs can unblock any task in their cell."""
    role = get_agent_role(agent_id)
    agent_cell = get_agent_cell(agent_id)
    task_team = task.get("team")

    # Main PM, Board, CEO can unblock anything
    if role in ("main_pm", "product_owner", "head_marketing", "auditor", "ceo"):
        return True, "OK"

    # Cell PM can unblock any task in their cell
    if role == "cell_pm" and agent_cell and agent_cell == task_team:
        return True, "OK"

    return False, "Only PMs can unblock tasks"


async def handle_task_unblock(
    client: ApiClient, task_id: str, agent_id: str
) -> dict[str, Any]:
    """Handle task unblocking."""
    task_resp = await client.get(f"/tasks/{task_id}")
    if task_resp.is_status(status.HTTP_404_NOT_FOUND):
        return format_error_response("NOT_FOUND", f"Task {task_id} not found")

    task = task_resp.json()

    if task.get("status") != "blocked":
        return format_error_response("INVALID_STATE", "Task is not blocked")

    # Check PM permissions or task ownership
    can_unblock, _ = _can_unblock_task(agent_id, task)
    if not can_unblock and await validate_task_ownership(task, agent_id, client):
        return format_error_response(
            "NOT_AUTHORIZED",
            "You cannot unblock this task. Must be assigned or a PM.",
        )

    unblock_resp = await client.post(f"/tasks/{task_id}/unblock")

    if not unblock_resp.ok:
        return format_error_response("UNBLOCK_FAILED", "Failed to unblock task")

    # Get the assigned developer to notify
    assigned_slug = task.get("assigned_to_slug")

    return format_task_response(
        unblock_resp.json(),
        "CONTINUE",
        "Task unblocked. Resume from last checkpoint.",
        a2a_suggestion=(
            f"Notify developer that blocker is resolved:\n"
            f"roboco_agent_request(target_agent='{assigned_slug or 'developer'}', "
            f"skill='resume', message='Blocker resolved for task {task_id}')"
        )
        if assigned_slug
        else None,
    )


async def handle_task_pause(
    client: ApiClient, data: TaskPauseInput, agent_id: str
) -> dict[str, Any]:
    """Handle task pausing."""
    task_resp = await client.get(f"/tasks/{data.task_id}")
    if task_resp.is_status(status.HTTP_404_NOT_FOUND):
        return format_error_response("NOT_FOUND", f"Task {data.task_id} not found")

    task = task_resp.json()

    if error := await validate_task_ownership(task, agent_id, client):
        return error

    if task.get("status") != "in_progress":
        return format_error_response(
            "INVALID_STATE", "Can only pause in_progress tasks"
        )

    await client.post(
        f"/tasks/{data.task_id}/checkpoint",
        json={
            "state_summary": data.checkpoint_summary,
            "remaining_work": data.remaining_work,
            "notes": data.reason,
        },
    )

    pause_resp = await client.post(f"/tasks/{data.task_id}/pause")

    if not pause_resp.ok:
        return format_error_response("PAUSE_FAILED", "Failed to pause task")

    return format_task_response(
        pause_resp.json(),
        "SCAN_FOR_WORK",
        f"Task paused. Checkpoint saved.\nReason: {data.reason}\n\n"
        "To resume later, call roboco_task_start with this task_id.\n"
        "Now call roboco_task_scan to find your next task.",
    )
