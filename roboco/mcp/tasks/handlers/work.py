"""
Task Work Handlers

Handlers for task planning, starting, and progress updates.
"""

from typing import Any

from roboco.mcp.tasks import MAX_PERCENTAGE, MIN_PERCENTAGE, format_task_response
from roboco.mcp.tasks.handlers._helpers import (
    build_plan_data,
    fetch_task_or_error,
    validate_task_ownership,
    validate_task_start,
    validate_task_status_claimed,
)
from roboco.mcp.utils import ApiClient, format_error_response

ACTIVE_PROGRESS_STATUSES = frozenset(
    {"in_progress", "verifying", "awaiting_qa", "awaiting_documentation"}
)


def _format_plan_response(
    updated_task: dict[str, Any], open_questions: list[str] | None
) -> dict[str, Any]:
    """Format plan save response based on whether there are open questions."""
    if open_questions:
        return format_task_response(
            updated_task,
            "ASK_QUESTIONS",
            f"Plan saved but you have {len(open_questions)} open question(s). "
            "Ask these questions in your cell channel before starting. "
            "Do NOT proceed until questions are answered.",
        )
    return format_task_response(
        updated_task,
        "START",
        "Plan saved. Call roboco_task_start to begin implementation.",
    )


async def handle_task_plan(
    client: ApiClient,
    task_id: str,
    plan_params: dict[str, Any],
    agent_id: str,
) -> dict[str, Any]:
    """Handle task planning."""
    task, error = await fetch_task_or_error(client, task_id)
    if error:
        return error
    assert task is not None

    if error := await validate_task_ownership(task, agent_id, client):
        return error
    if error := validate_task_status_claimed(task):
        return error

    plan_data = build_plan_data(plan_params)
    update_resp = await client.patch(f"/tasks/{task_id}", json={"plan": plan_data})
    if not update_resp.ok:
        return format_error_response(
            "UPDATE_FAILED", "Failed to save plan", {"api_error": update_resp.text}
        )

    updated_task = update_resp.json()
    return _format_plan_response(updated_task, plan_params.get("open_questions"))


async def handle_task_start(
    client: ApiClient, task_id: str, agent_id: str
) -> dict[str, Any]:
    """Handle task start."""
    task, error = await fetch_task_or_error(client, task_id)
    if error:
        return error
    assert task is not None

    if error := await validate_task_start(task, agent_id, client):
        return error

    start_resp = await client.post(f"/tasks/{task_id}/start")
    if not start_resp.ok:
        return format_error_response(
            "START_FAILED", "Failed to start task", {"api_error": start_resp.text}
        )

    return format_task_response(
        start_resp.json(),
        "EXECUTE",
        "Task started. Work through your plan step by step:\n"
        "1. Implement each sub-task\n"
        "2. Commit frequently with clear messages\n"
        "3. Call roboco_task_progress to update status\n"
        "4. If blocked, call roboco_task_block immediately\n"
        "5. When done, call roboco_task_submit_verification",
    )


def _validate_percentage(percentage: int) -> dict[str, Any] | None:
    """Validate progress percentage. Returns error or None."""
    if not MIN_PERCENTAGE <= percentage <= MAX_PERCENTAGE:
        return format_error_response(
            "INVALID_PERCENTAGE",
            f"Percentage must be between {MIN_PERCENTAGE} and {MAX_PERCENTAGE}",
        )
    return None


def _validate_active_status(task: dict[str, Any]) -> dict[str, Any] | None:
    """Validate task is in an active status for progress updates."""
    if task.get("status") not in ACTIVE_PROGRESS_STATUSES:
        return format_error_response(
            "INVALID_STATE",
            f"Can only update progress for active tasks. Current: {task.get('status')}",
        )
    return None


async def handle_task_progress(
    client: ApiClient,
    task_id: str,
    message: str,
    percentage: int,
    agent_id: str,
) -> dict[str, Any]:
    """Handle task progress update."""
    if error := _validate_percentage(percentage):
        return error

    task, error = await fetch_task_or_error(client, task_id)
    if error:
        return error
    assert task is not None

    if error := await validate_task_ownership(task, agent_id, client):
        return error

    if error := _validate_active_status(task):
        return error

    progress_resp = await client.post(
        f"/tasks/{task_id}/progress",
        json={"agent_id": agent_id, "message": message, "percentage": percentage},
    )

    if not progress_resp.ok:
        return format_error_response("UPDATE_FAILED", "Failed to update progress")

    return format_task_response(
        progress_resp.json(), "CONTINUE", "Progress recorded. Keep working."
    )
