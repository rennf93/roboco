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
    if task is None:
        raise RuntimeError("Invariant: task must be set")

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


async def _safe_checkout(
    client: ApiClient,
    project_slug: str,
    branch_name: str,
    agent_id: str,
) -> dict[str, Any] | None:
    """Safely checkout branch with pre-flight checks.

    Returns error dict if checkout should be blocked, None if successful.
    """
    # 1. Get current git status
    status_resp = await client.get("/git/status", params={"project_slug": project_slug})
    if not status_resp.ok:
        return format_error_response(
            "GIT_STATUS_FAILED",
            "Cannot verify git status before checkout",
            {"status_code": status_resp.status_code, "detail": status_resp.text},
        )

    status_data = status_resp.json()

    # 2. Check for uncommitted changes (staged or unstaged)
    staged = status_data.get("staged_files", [])
    unstaged = status_data.get("unstaged_files", [])
    if staged or unstaged:
        return format_error_response(
            "UNCOMMITTED_CHANGES",
            "Cannot checkout: uncommitted changes in workspace. "
            "Commit or stash your changes before starting this task.",
            {
                "staged_files": staged[:5],  # Limit to first 5
                "unstaged_files": unstaged[:5],
                "current_branch": status_data.get("current_branch"),
            },
            hint="Use roboco_git_commit() or git stash before starting.",
        )

    # 3. Checkout the branch (API will fetch if needed)
    checkout_resp = await client.post(
        "/git/checkout",
        json={
            "project_slug": project_slug,
            "branch": branch_name,
            "agent_id": agent_id,
        },
    )

    if not checkout_resp.ok:
        return format_error_response(
            "CHECKOUT_FAILED",
            f"Failed to checkout branch '{branch_name}'",
            {"status_code": checkout_resp.status_code, "detail": checkout_resp.text},
            hint="Branch may not exist. Re-claim or check if parent needs claiming.",
        )

    # Success - no error
    return None


async def handle_task_start(
    client: ApiClient, task_id: str, agent_id: str
) -> dict[str, Any]:
    """Handle task start with auto-checkout."""
    task, error = await fetch_task_or_error(client, task_id)
    if error:
        return error
    if task is None:
        raise RuntimeError("Invariant: task must be set")

    if error := await validate_task_start(task, agent_id, client):
        return error

    # Auto-checkout for task (all tasks follow git workflow)
    branch_name = task.get("branch_name")
    project_slug = task.get("project_slug")

    if branch_name and project_slug:
        checkout_error = await _safe_checkout(
            client, project_slug, branch_name, agent_id
        )
        if checkout_error:
            return checkout_error

    start_resp = await client.post(f"/tasks/{task_id}/start")
    if not start_resp.ok:
        return format_error_response(
            "START_FAILED", "Failed to start task", {"api_error": start_resp.text}
        )

    # Build guidance with git checkout info
    guidance = "Task started. Work through your plan step by step:\n"
    if branch_name:
        guidance += (
            f"✓ Checked out branch: {branch_name}\n"
            f"   Workspace: /data/workspaces/{project_slug}/...\n"
            "   Use roboco_git_* tools for git operations.\n\n"
        )
    guidance += (
        "1. Implement each sub-task\n"
        "2. Use roboco_git_commit() to commit frequently\n"
        "3. Call roboco_task_progress to update status\n"
        "4. If blocked, call roboco_task_block immediately\n"
        "5. When done, call roboco_task_submit_verification"
    )

    return format_task_response(start_resp.json(), "EXECUTE", guidance)


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
    if task is None:
        raise RuntimeError("Invariant: task must be set")

    if error := await validate_task_ownership(task, agent_id, client):
        return error

    if error := _validate_active_status(task):
        return error

    progress_resp = await client.post(
        f"/tasks/{task_id}/progress",
        json={"message": message, "percentage": percentage},
    )

    if not progress_resp.ok:
        return format_error_response("UPDATE_FAILED", "Failed to update progress")

    guidance = (
        "Progress recorded. Keep working.\n\n"
        "TIPS:\n"
        "- Use roboco_journal_entry() to log decisions as you go\n"
        "- Hit an error? Try roboco_search_error(pattern) for solutions\n"
        "- Need context? Use roboco_kb_search(query) to find related code/docs"
    )
    return format_task_response(progress_resp.json(), "CONTINUE", guidance)
