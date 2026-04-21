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
            hint="roboco_kb_search('task lifecycle role permissions')",
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
            hint="roboco_kb_search('pm role permissions')",
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

    # Determine PM agent based on documenter's team
    team_prefix = agent_id[:2] if agent_id else "be"
    pm_agent = f"{team_prefix}-pm"

    return format_task_response(
        docs_resp.json(),
        "AWAITING_PM",
        "Documentation complete! Task is now awaiting PM review.\n"
        "The Cell PM will review and complete the task.\n\n"
        "REMINDER: Did you index your docs for RAG search?\n"
        "  roboco_kb_index_docs(['/docs/backend/your-doc.md'])\n"
        "You can still index after submitting - unindexed docs won't be searchable!\n\n"
        "Call roboco_task_scan for next documentation task.",
        a2a_suggestion=(
            f"Notify PM that docs are ready:\n"
            f"roboco_agent_request(target_agent='{pm_agent}', "
            f"skill='coordination', message='Task {task_id} ready for review')"
        ),
    )


def _is_pm_own_task(task: dict[str, Any], agent_id: str) -> bool:
    """Check if this is the PM's own task (assigned to them)."""
    assigned_to = task.get("assigned_to")
    # Could be UUID or slug - check both patterns
    return assigned_to == agent_id or (
        isinstance(assigned_to, str) and agent_id in assigned_to
    )


async def _check_descendants_completed(
    client: ApiClient, task_id: str
) -> dict[str, Any] | None:
    """Check ALL descendants (recursive) of a task are in terminal states.

    Returns error if any descendants are not completed/cancelled, None if OK.
    """
    try:
        resp = await client.get(f"/tasks/{task_id}/descendants")
        if not resp.ok:
            return None

        descendants = resp.json()
        if not descendants:
            return None

        # Check for incomplete (not completed, not cancelled)
        incomplete = [
            {
                "id": str(task.get("id", "unknown")),
                "title": task.get("title", "Untitled"),
                "status": task.get("status") or "unknown",
            }
            for task in descendants
            if task.get("status") not in ("completed", "cancelled")
        ]

        if incomplete:
            return format_error_response(
                "INCOMPLETE_DESCENDANTS",
                f"Cannot complete: {len(incomplete)} descendant(s) still in progress.",
                {
                    "incomplete_descendants": incomplete[:10],  # Limit to 10
                    "guidance": (
                        "ALL descendants (subtasks, sub-subtasks, etc.) must be "
                        "COMPLETED or CANCELLED before completing parent."
                    ),
                },
            )

        # All descendants in terminal states (completed/cancelled) - allow completion
        return None
    except Exception:
        return None


async def _validate_descendants_or_force(
    client: ApiClient, task_id: str, force: bool, justification: str | None
) -> dict[str, Any] | None:
    """Validate all descendants are in terminal states, or force override."""
    if force:
        if not justification:
            return format_error_response(
                "JUSTIFICATION_REQUIRED",
                "force_with_cancelled requires justification explaining "
                "why cancelled descendants don't block completion.",
            )
        return None
    return await _check_descendants_completed(client, task_id)


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
        hint="roboco_kb_search('task status lifecycle')",
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

    if error := await _validate_descendants_or_force(
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


# =============================================================================
# CEO APPROVAL WORKFLOW
# =============================================================================


def _validate_ceo_role(agent_id: str) -> dict[str, Any] | None:
    """Validate agent is CEO. Returns error or None."""
    agent_role = get_agent_role(agent_id)
    if agent_role != "ceo":
        return format_error_response(
            "NOT_CEO",
            "Only CEO can perform this action.",
            {"your_role": agent_role},
        )
    return None


async def handle_escalate_to_ceo(
    client: ApiClient,
    task_id: str,
    agent_id: str,
    notes: str | None = None,
) -> dict[str, Any]:
    """Handle PM escalation to CEO for final approval.

    Used for major tasks requiring CEO sign-off:
    - Parent tasks with subtasks
    - High-priority features
    - Breaking changes
    """
    if error := _validate_pm_role(agent_id, "escalate to CEO"):
        return error

    task, error = await fetch_task_or_error(client, task_id)
    if error:
        return error
    assert task is not None

    current_status = task.get("status")
    if current_status != "awaiting_pm_review":
        return format_error_response(
            "INVALID_STATE",
            f"Cannot escalate to CEO - task is '{current_status}', "
            "expected 'awaiting_pm_review'.",
            {"current_status": current_status},
        )

    # Only parent tasks can be escalated to CEO (not subtasks)
    if task.get("parent_task_id"):
        return format_error_response(
            "IS_SUBTASK",
            "Cannot escalate subtask to CEO - only parent tasks allowed.",
            {
                "task_id": task_id,
                "parent_task_id": task.get("parent_task_id"),
                "guidance": "Escalate the parent task instead.",
            },
            hint="roboco_kb_search('parent task escalation')",
        )

    payload = {}
    if notes:
        payload["notes"] = notes

    resp = await client.post(f"/tasks/{task_id}/escalate-to-ceo", json=payload)
    if not resp.ok:
        return format_error_response(
            "ESCALATE_FAILED",
            "Failed to escalate to CEO",
            {"status_code": resp.status_code, "api_error": resp.text},
        )

    guidance = (
        "Task escalated to CEO for final approval.\n"
        "The CEO will review and either approve (complete) or reject (revision).\n"
        "You can continue with other tasks via roboco_task_scan."
    )
    return format_task_response(resp.json(), "AWAITING_CEO_APPROVAL", guidance)


async def handle_pm_reject(
    client: ApiClient,
    task_id: str,
    agent_id: str,
    notes: str | None = None,
) -> dict[str, Any]:
    """PM sends a task back to the developer for rework.

    Transitions `awaiting_pm_review → needs_revision`. The original dev
    (tracked via `quick_context.original_developer`) gets a high-priority
    notification and will pick the task back up on their next scan.
    """
    if error := _validate_pm_role(agent_id, "pm_reject"):
        return error

    task, error = await fetch_task_or_error(client, task_id)
    if error:
        return error
    assert task is not None

    current_status = task.get("status")
    if current_status != "awaiting_pm_review":
        return format_error_response(
            "INVALID_STATE",
            f"Cannot pm_reject - task is '{current_status}', "
            "expected 'awaiting_pm_review'.",
            {"current_status": current_status},
        )

    payload: dict[str, Any] = {}
    if notes:
        payload["notes"] = notes

    resp = await client.post(f"/tasks/{task_id}/pm-reject", json=payload)
    if not resp.ok:
        return format_error_response(
            "PM_REJECT_FAILED",
            "Failed to reject task back to dev",
            {"status_code": resp.status_code, "api_error": resp.text},
        )

    guidance = (
        "Task sent back to the developer as needs_revision. They will "
        "reclaim on their next scan and address your notes. Continue "
        "with other work via roboco_task_scan."
    )
    return format_task_response(resp.json(), "NEEDS_REVISION", guidance)


async def handle_ceo_approve(
    client: ApiClient,
    task_id: str,
    agent_id: str,
    notes: str | None = None,
) -> dict[str, Any]:
    """Handle CEO approval of a task.

    Final approval step - completes the task.
    """
    if error := _validate_ceo_role(agent_id):
        return error

    task, error = await fetch_task_or_error(client, task_id)
    if error:
        return error
    assert task is not None

    current_status = task.get("status")
    if current_status != "awaiting_ceo_approval":
        return format_error_response(
            "INVALID_STATE",
            f"Cannot approve - task is '{current_status}', "
            "expected 'awaiting_ceo_approval'.",
            {"current_status": current_status},
        )

    payload = {}
    if notes:
        payload["notes"] = notes

    resp = await client.post(f"/tasks/{task_id}/ceo-approve", json=payload)
    if not resp.ok:
        return format_error_response(
            "APPROVE_FAILED",
            "Failed to approve task",
            {"status_code": resp.status_code, "api_error": resp.text},
        )

    return format_task_response(
        resp.json(),
        "DONE",
        "Task approved and completed by CEO.\n"
        "Use roboco_task_scan to review other pending approvals.",
    )


async def handle_ceo_reject(
    client: ApiClient,
    task_id: str,
    agent_id: str,
    reason: str,
) -> dict[str, Any]:
    """Handle CEO rejection of a task.

    Sends task back for revision with feedback.
    """
    if error := _validate_ceo_role(agent_id):
        return error

    if not reason or not reason.strip():
        return format_error_response(
            "REASON_REQUIRED",
            "CEO rejection requires a reason explaining what needs fixing.",
        )

    task, error = await fetch_task_or_error(client, task_id)
    if error:
        return error
    assert task is not None

    current_status = task.get("status")
    if current_status != "awaiting_ceo_approval":
        return format_error_response(
            "INVALID_STATE",
            f"Cannot reject - task is '{current_status}', "
            "expected 'awaiting_ceo_approval'.",
            {"current_status": current_status},
        )

    resp = await client.post(f"/tasks/{task_id}/ceo-reject", json={"notes": reason})
    if not resp.ok:
        return format_error_response(
            "REJECT_FAILED",
            "Failed to reject task",
            {"status_code": resp.status_code, "api_error": resp.text},
        )

    # Get the original developer from quick_context to suggest A2A notification
    quick_context = task.get("quick_context", "")
    from roboco.services.task import extract_original_developer

    original_dev = extract_original_developer(quick_context)

    return format_task_response(
        resp.json(),
        "NEEDS_REVISION",
        f"Task rejected and returned for revision.\nReason: {reason}\n"
        "The developer will address feedback and resubmit.",
        a2a_suggestion=(
            f"Notify developer immediately (urgent):\n"
            f"roboco_agent_request('{original_dev}', 'revision', "
            f"'CEO rejected: {reason[:50]}...', options={{'urgent': True}})"
        )
        if original_dev
        else None,
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


# Valid cancellation reasons - PMs must justify cancellations
VALID_CANCEL_REASONS = {
    "duplicate",  # Task duplicates existing work
    "obsolete",  # Requirements changed, task no longer needed
    "blocked_permanently",  # External dependency that won't be resolved
    "reassigned",  # Work moved to different task/approach
    "scope_change",  # Project scope changed, task out of scope
    "stakeholder_request",  # CEO/Board requested cancellation
}


def _validate_cancel_reason(reason: str | None) -> dict[str, Any] | None:
    """Validate cancellation reason is provided and legitimate."""
    if not reason or not reason.strip():
        return format_error_response(
            "REASON_REQUIRED",
            "Task cancellation requires a reason. Provide one of: "
            + ", ".join(sorted(VALID_CANCEL_REASONS))
            + " followed by details.",
            {
                "valid_reasons": sorted(VALID_CANCEL_REASONS),
                "example": "obsolete: requirements changed in TASK-123",
            },
        )

    # Check reason starts with a valid category
    reason_lower = reason.lower().strip()
    has_valid_prefix = any(reason_lower.startswith(r) for r in VALID_CANCEL_REASONS)
    if not has_valid_prefix:
        return format_error_response(
            "INVALID_REASON",
            "Cancellation reason must start with a valid category: "
            + ", ".join(sorted(VALID_CANCEL_REASONS)),
            {
                "provided": reason[:50],
                "valid_reasons": sorted(VALID_CANCEL_REASONS),
                "example": "duplicate: same as TASK-456",
            },
        )
    return None


def _validate_not_active_work(
    task: dict[str, Any], agent_id: str
) -> dict[str, Any] | None:
    """Block cancellation of tasks with active work unless escalated.

    Tasks in 'in_progress' with an assignee other than the canceller
    should not be cancelled - the assignee should pause/block first.
    """
    current_status = task.get("status")
    assigned_to = task.get("assigned_to")

    # Allow cancellation of pending/claimed tasks freely (with reason)
    if current_status in ("pending", "claimed"):
        return None

    # If task is in active work states and assigned to someone else,
    # require the work to be paused/blocked first
    active_states = {
        "in_progress",
        "verifying",
        "awaiting_qa",
        "awaiting_documentation",
        "awaiting_pm_review",
    }

    if current_status in active_states and assigned_to:
        # Check if canceller is NOT the assignee
        is_own_task = assigned_to == agent_id or (
            isinstance(assigned_to, str) and agent_id in assigned_to
        )
        if not is_own_task:
            return format_error_response(
                "ACTIVE_WORK_PROTECTED",
                f"Cannot cancel task in '{current_status}' - someone is working on it. "
                "Ask the assignee to pause/block the task first, or use escalation.",
                {
                    "assigned_to": assigned_to,
                    "current_status": current_status,
                    "alternatives": [
                        "Ask assignee to roboco_task_pause() or roboco_task_block()",
                        "Use roboco_task_escalate() to involve higher management",
                        "Wait for task to be paused/blocked, then cancel",
                    ],
                },
            )
    return None


async def _validate_cancel_request(
    client: ApiClient, task_id: str, agent_id: str, reason: str | None
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Validate all cancellation prerequisites. Returns (task, error)."""
    # Check PM role
    if error := _validate_pm_role(agent_id, "cancel tasks"):
        return None, error

    # Require a valid reason - no arbitrary cancellations
    if error := _validate_cancel_reason(reason):
        return None, error

    task, error = await fetch_task_or_error(client, task_id)
    if error:
        return None, error
    assert task is not None

    if error := _validate_task_cancellable(task):
        return None, error

    # Protect active work from arbitrary cancellation
    if error := _validate_not_active_work(task, agent_id):
        return None, error

    return task, None


async def handle_task_cancel(
    client: ApiClient, task_id: str, agent_id: str, reason: str | None = None
) -> dict[str, Any]:
    """Handle task cancellation (PM and board only).

    Cancellation requires:
    1. A valid reason category (duplicate, obsolete, blocked_permanently, etc.)
    2. Task not actively being worked on by someone else

    If task is in_progress with another assignee, they must pause/block first.
    """
    _, error = await _validate_cancel_request(client, task_id, agent_id, reason)
    if error:
        return error

    # Include reason in the API call
    cancel_resp = await client.post(
        f"/tasks/{task_id}/cancel",
        json={"reason": reason},
    )
    if not cancel_resp.ok:
        return format_error_response(
            "CANCEL_FAILED",
            "Failed to cancel task",
            {"status_code": cancel_resp.status_code, "api_error": cancel_resp.text},
        )

    return format_task_response(
        cancel_resp.json(),
        "CANCELLED",
        f"Task cancelled. Reason: {reason}",
    )


async def _check_in_progress_tasks(client: ApiClient) -> dict[str, Any] | None:
    """Check for in-progress tasks. Returns error or None.

    If the scan itself fails (API unreachable etc.), we log and conservatively
    allow the idle — an unreachable API is a bigger problem that the agent
    can't resolve here, and blocking the idle handshake only compounds it.
    """
    import structlog

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
    except Exception as e:
        structlog.get_logger().warning(
            "Failed to check in-progress tasks before idle",
            error=str(e),
        )
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
