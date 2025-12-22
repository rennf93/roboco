"""
Task Lifecycle Handlers

Handlers for task completion, cancellation, and agent idle state.
"""

from typing import Any

from fastapi import status

from roboco.agents_config import can_cancel_tasks, get_agent_role
from roboco.mcp.tasks import format_task_response
from roboco.mcp.tasks.handlers._helpers import fetch_task_or_error, validate_task_status
from roboco.mcp.utils import ApiClient, format_error_response


def _validate_documenter_role(agent_id: str) -> dict[str, Any] | None:
    """Validate agent is a documenter. Returns error or None."""
    agent_role = get_agent_role(agent_id)
    if agent_role != "documenter":
        return format_error_response(
            "NOT_DOCUMENTER",
            "Only documenters can mark documentation as complete.",
            {"your_role": agent_role},
        )
    return None


def _validate_pm_role(agent_id: str, action: str) -> dict[str, Any] | None:
    """Validate agent has PM permissions. Returns error or None."""
    if not can_cancel_tasks(agent_id):
        role = get_agent_role(agent_id)
        return format_error_response(
            "NOT_AUTHORIZED",
            f"Only PMs and board members can {action}",
            {"your_role": role},
        )
    return None


async def handle_docs_complete(
    client: ApiClient, task_id: str, agent_id: str, doc_notes: str | None = None
) -> dict[str, Any]:
    """Handle documentation completion (documenter only)."""
    if error := _validate_documenter_role(agent_id):
        return error

    task, error = await fetch_task_or_error(client, task_id)
    if error:
        return error
    assert task is not None

    if error := validate_task_status(
        task, "awaiting_documentation", "mark as docs complete"
    ):
        return error

    payload = {"notes": doc_notes} if doc_notes else {}
    docs_resp = await client.post(f"/tasks/{task_id}/docs-complete", json=payload)

    if not docs_resp.ok:
        return format_error_response(
            "DOCS_COMPLETE_FAILED",
            "Failed to mark documentation complete",
            {"status_code": docs_resp.status_code, "api_error": docs_resp.text},
        )

    return format_task_response(
        docs_resp.json(),
        "AWAITING_PM",
        "Documentation complete! Task is now awaiting PM review.\n"
        "The Cell PM will review and complete the task.\n"
        "Call roboco_task_scan for next documentation task.",
    )


async def handle_task_complete(
    client: ApiClient, task_id: str, agent_id: str
) -> dict[str, Any]:
    """Handle task completion (PM only)."""
    if error := _validate_pm_role(agent_id, "complete tasks"):
        return error

    task, error = await fetch_task_or_error(client, task_id)
    if error:
        return error
    assert task is not None

    if error := validate_task_status(task, "awaiting_pm_review", "complete"):
        return error

    complete_resp = await client.post(f"/tasks/{task_id}/complete")
    if not complete_resp.ok:
        return format_error_response(
            "COMPLETE_FAILED",
            "Failed to complete task",
            {"status_code": complete_resp.status_code, "api_error": complete_resp.text},
        )

    return format_task_response(
        complete_resp.json(),
        "DONE",
        "Task completed successfully!\nCall roboco_task_scan for more work.",
    )


def _validate_task_cancellable(task: dict[str, Any]) -> dict[str, Any] | None:
    """Validate task can be cancelled. Returns error or None."""
    current_status = task.get("status")
    if current_status in ("completed", "cancelled"):
        return format_error_response(
            "INVALID_STATE",
            f"Cannot cancel task in '{current_status}' status",
        )
    return None


async def handle_task_cancel(
    client: ApiClient, task_id: str, agent_id: str, reason: str | None = None
) -> dict[str, Any]:
    """Handle task cancellation (PM and board only)."""
    if error := _validate_pm_role(agent_id, "cancel tasks"):
        return error

    task, error = await fetch_task_or_error(client, task_id)
    if error:
        return error
    assert task is not None

    if error := _validate_task_cancellable(task):
        return error

    cancel_resp = await client.post(f"/tasks/{task_id}/cancel")
    if not cancel_resp.ok:
        return format_error_response(
            "CANCEL_FAILED",
            "Failed to cancel task",
            {"status_code": cancel_resp.status_code, "api_error": cancel_resp.text},
        )

    return format_task_response(
        cancel_resp.json(),
        "CANCELLED",
        f"Task cancelled.{' Reason: ' + reason if reason else ''}",
    )


async def _check_in_progress_tasks(client: ApiClient) -> dict[str, Any] | None:
    """Check for in-progress tasks. Returns error or None."""
    try:
        scan_resp = await client.get("/tasks/my", params={"status": "in_progress"})
        if scan_resp.ok:
            tasks = scan_resp.json()
            if tasks:
                task_info = [
                    {"id": t.get("id"), "title": t.get("title")} for t in tasks
                ]
                return format_error_response(
                    "TASKS_IN_PROGRESS",
                    "You have in-progress tasks. Handle them before going idle.",
                    {"tasks": task_info},
                )
    except Exception:
        pass
    return None


def _format_idle_response(resp: Any) -> dict[str, Any]:
    """Format response based on orchestrator status code."""
    if resp.is_status(status.HTTP_204_NO_CONTENT):
        return {
            "status": "idle",
            "message": (
                "You are now in WAITING state. Your container will terminate "
                "to save resources. You will be respawned when work is available."
            ),
            "action": "EXIT_GRACEFULLY",
        }

    if resp.is_status(status.HTTP_503_SERVICE_UNAVAILABLE):
        return format_error_response(
            "ORCHESTRATOR_UNAVAILABLE",
            "Orchestrator is not running. Cannot mark idle state.",
            {"detail": resp.text},
        )

    return format_error_response(
        "IDLE_FAILED",
        "Failed to signal idle state to orchestrator",
        {"status_code": resp.status_code, "detail": resp.text},
    )


async def handle_agent_idle(client: ApiClient, agent_id: str) -> dict[str, Any]:
    """Handle agent going idle (no work available)."""
    if error := await _check_in_progress_tasks(client):
        return error

    try:
        resp = await client.post(
            f"/orchestrator/agents/{agent_id}/mark-waiting",
            params={"waiting_for": "task_assignment"},
        )
    except Exception as e:
        return format_error_response(
            "CONNECTION_ERROR",
            f"Failed to connect to orchestrator: {type(e).__name__}",
        )

    return _format_idle_response(resp)
