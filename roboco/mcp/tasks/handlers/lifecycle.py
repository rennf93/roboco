"""
Task Lifecycle Handlers

Handlers for task completion, cancellation, and agent idle state.
"""

from typing import Any

from fastapi import status

from roboco.agents_config import can_cancel_tasks, get_agent_role
from roboco.mcp.tasks import format_task_response
from roboco.mcp.tasks.handlers._helpers import (
    fetch_task_or_error,
    validate_task_status_in,
)
from roboco.mcp.utils import ApiClient, format_error_response

# Documenter workflow: awaiting_documentation → claim → plan → start → docs_complete
DOCUMENTER_WORKFLOW_STATUSES = {"awaiting_documentation", "claimed", "in_progress"}


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

    if error := validate_task_status_in(
        task, DOCUMENTER_WORKFLOW_STATUSES, "mark as docs complete"
    ):
        return error

    # Only send payload if doc_notes provided (QANotes.notes is required)
    if doc_notes:
        docs_resp = await client.post(
            f"/tasks/{task_id}/docs-complete", json={"notes": doc_notes}
        )
    else:
        docs_resp = await client.post(f"/tasks/{task_id}/docs-complete")

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


def _is_pm_own_task(task: dict[str, Any], agent_id: str) -> bool:
    """Check if this is the PM's own task (assigned to them)."""
    assigned_to = task.get("assigned_to")
    # Could be UUID or slug - check both patterns
    return assigned_to == agent_id or (
        isinstance(assigned_to, str) and agent_id in assigned_to
    )


async def _check_children_completed(
    client: ApiClient, task_id: str
) -> dict[str, Any] | None:
    """Check ALL children of a task are completed.

    Cancelled subtasks also block completion - they must be resolved first.
    Returns error if any children are not completed, None if OK.
    """
    try:
        resp = await client.get(f"/tasks/{task_id}/subtasks")
        if not resp.ok:
            return None

        subtasks = resp.json()
        if not subtasks:
            return None

        incomplete = [
            {
                "id": str(subtask.get("id", "unknown")),
                "title": subtask.get("title", "Untitled"),
                "status": subtask.get("status") or "unknown",
            }
            for subtask in subtasks
            if subtask.get("status") != "completed"
        ]

        if incomplete:
            return format_error_response(
                "INCOMPLETE_CHILDREN",
                f"Cannot complete task: {len(incomplete)} subtask(s) not completed.",
                {
                    "incomplete_subtasks": incomplete,
                    "guidance": (
                        "ALL subtasks must be COMPLETED before completing parent. "
                        "Cancelled subtasks must be resolved or removed first."
                    ),
                },
            )

        return None
    except Exception:
        return None


async def _validate_children_or_force(
    client: ApiClient, task_id: str, force: bool, justification: str | None
) -> dict[str, Any] | None:
    """Validate children completion or force override. Returns error or None."""
    if force:
        if not justification:
            return format_error_response(
                "JUSTIFICATION_REQUIRED",
                "force_with_cancelled requires justification explaining "
                "why cancelled subtasks don't block completion.",
            )
        return None
    return await _check_children_completed(client, task_id)


def _validate_completion_status(
    task: dict[str, Any], agent_id: str
) -> dict[str, Any] | None:
    """Validate task is in completable status. Returns error or None."""
    current_status = task.get("status")
    is_own_task = current_status == "in_progress" and _is_pm_own_task(task, agent_id)
    is_review_task = current_status == "awaiting_pm_review"

    if is_own_task or is_review_task:
        return None

    return format_error_response(
        "INVALID_STATE",
        f"Cannot complete task in '{current_status}' status. "
        "Expected 'awaiting_pm_review' (dev work) or 'in_progress' (own task).",
        {"current_status": current_status},
    )


async def handle_task_complete(
    client: ApiClient,
    task_id: str,
    agent_id: str,
    force_with_cancelled: bool = False,
    justification: str | None = None,
) -> dict[str, Any]:
    """Handle task completion (PM only).

    Two completion paths:
    1. Completing developer work: task must be in 'awaiting_pm_review'
    2. Completing PM's own task: task can be in 'in_progress' if assigned to PM

    PM Override for cancelled subtasks:
    Use force_with_cancelled=True with justification to complete despite
    cancelled subtasks. Only works if ALL non-completed children are cancelled.
    """
    if error := _validate_pm_role(agent_id, "complete tasks"):
        return error

    task, error = await fetch_task_or_error(client, task_id)
    if error:
        return error
    assert task is not None

    if error := _validate_completion_status(task, agent_id):
        return error

    if error := await _validate_children_or_force(
        client, task_id, force_with_cancelled, justification
    ):
        return error

    payload: dict[str, Any] = {}
    if force_with_cancelled:
        payload = {"force_with_cancelled": True, "justification": justification}

    complete_resp = await client.post(f"/tasks/{task_id}/complete", json=payload)
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


def _validate_not_qa_on_dev_work(
    task: dict[str, Any], agent_id: str
) -> dict[str, Any] | None:
    """Validate QA agents don't bypass the proper QA workflow for dev tasks.

    If a QA agent is working on a task that was previously developer work
    (indicated by self_verified=True), they MUST use roboco_task_qa_pass or
    roboco_task_qa_fail, not submit_pm_review.
    """
    agent_role = get_agent_role(agent_id)
    if agent_role != "qa":
        return None  # Not QA, allow

    # Check if this is dev work that went through verification
    if task.get("self_verified"):
        return format_error_response(
            "USE_QA_TOOLS",
            "This is developer work that went through QA queue. "
            "Use roboco_task_qa_pass or roboco_task_qa_fail instead.",
            {"hint": "qa_pass sends to documenter, qa_fail returns to dev"},
        )
    return None


async def handle_submit_pm_review(
    client: ApiClient, task_id: str, agent_id: str, notes: str | None = None
) -> dict[str, Any]:
    """Handle direct submission for PM review.

    For tasks that don't follow the standard dev→QA→docs workflow,
    such as PM validation tasks, QA audit tasks, or directly-assigned work.

    IMPORTANT: QA agents reviewing dev work (self_verified=True) must use
    roboco_task_qa_pass/qa_fail instead - this ensures documenter phase.
    """
    task, error = await fetch_task_or_error(client, task_id)
    if error:
        return error
    assert task is not None

    # QA agents reviewing dev work must use qa_pass/qa_fail
    if error := _validate_not_qa_on_dev_work(task, agent_id):
        return error

    # Must be in_progress to submit for PM review
    current_status = task.get("status")
    if current_status != "in_progress":
        return format_error_response(
            "INVALID_STATE",
            f"Cannot submit for PM review - task is '{current_status}', "
            "expected 'in_progress'.",
            {"current_status": current_status},
        )

    # Submit to API
    payload = {}
    if notes:
        payload["notes"] = notes

    resp = await client.post(f"/tasks/{task_id}/submit-pm-review", json=payload)
    if not resp.ok:
        return format_error_response(
            "SUBMIT_FAILED",
            "Failed to submit for PM review",
            {"status_code": resp.status_code, "api_error": resp.text},
        )

    guidance = (
        "Task submitted for PM review. The PM will verify and complete the task.\n"
        "Call roboco_task_scan to find more work, or roboco_agent_idle if none."
    )
    return format_task_response(resp.json(), "AWAITING_PM_REVIEW", guidance)


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
