"""
Task Blocking Handlers

Handlers for blocking, unblocking, and pausing tasks.
"""

from typing import Any

from fastapi import status

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

    return format_task_response(
        block_resp.json(),
        "WAIT_OR_SWITCH",
        f"Task blocked: {data.reason}\n\n"
        "Options:\n"
        "1. WAIT - If resolution expected soon, poll for updates\n"
        "2. SWITCH - Call roboco_task_scan to work on another task\n"
        "3. ESCALATE - Message your PM if this is urgent\n\n"
        "The blocker has been communicated. You'll be notified when resolved.",
    )


async def handle_task_unblock(
    client: ApiClient, task_id: str, agent_id: str
) -> dict[str, Any]:
    """Handle task unblocking."""
    task_resp = await client.get(f"/tasks/{task_id}")
    if task_resp.is_status(status.HTTP_404_NOT_FOUND):
        return format_error_response("NOT_FOUND", f"Task {task_id} not found")

    task = task_resp.json()

    if error := await validate_task_ownership(task, agent_id, client):
        return error

    if task.get("status") != "blocked":
        return format_error_response("INVALID_STATE", "Task is not blocked")

    unblock_resp = await client.post(f"/tasks/{task_id}/unblock")

    if not unblock_resp.ok:
        return format_error_response("UNBLOCK_FAILED", "Failed to unblock task")

    return format_task_response(
        unblock_resp.json(), "CONTINUE", "Task unblocked. Resume from last checkpoint."
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
            "agent_id": agent_id,
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
