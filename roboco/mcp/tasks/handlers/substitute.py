"""
Task Substitute Handler

Handler for agent substitution requests.
Allows agents to release tasks gracefully when they can't continue.
Bypasses the "can't claim while in_progress" rule.
"""

from dataclasses import dataclass
from typing import Any

from roboco.mcp.tasks import format_task_response
from roboco.mcp.tasks.handlers._helpers import (
    fetch_task_or_error,
    resolve_agent_uuid_cached,
)
from roboco.mcp.utils import ApiClient, format_error_response
from roboco.models import SubstituteReason, TaskStatus

# HTTP status code for "Not Found"
HTTP_NOT_FOUND = 404

# Map substitute reasons to target task statuses
# NOTE: OUT_OF_SCOPE_ROLE goes to awaiting_pm_review to avoid reassignment loops
# (e.g., documenter can't self-document → substitute → gets reassigned same doc → loop)
REASON_TO_STATUS: dict[SubstituteReason, TaskStatus] = {
    SubstituteReason.TASK_COMPLETE: TaskStatus.AWAITING_QA,
    SubstituteReason.LOW_CONTEXT: TaskStatus.PENDING,
    SubstituteReason.OUT_OF_SCOPE_TEAM: TaskStatus.PENDING,
    SubstituteReason.OUT_OF_SCOPE_ROLE: TaskStatus.AWAITING_PM_REVIEW,  # PM decides
    SubstituteReason.MAX_RETRIES: TaskStatus.PENDING,
    SubstituteReason.BLOCKED_EXTERNAL: TaskStatus.BLOCKED,
}


@dataclass
class SubstituteRequest:
    """Request data for substitution."""

    task_id: str
    agent_id: str
    reason: SubstituteReason
    details: str
    suggested_role: str | None = None
    suggested_team: str | None = None


async def _validate_substitute_request(
    task: dict[str, Any],
    agent_id: str,
    reason: str,
    client: ApiClient,
) -> dict[str, Any] | None:
    """Validate substitution request. Returns error or None."""
    # Validate reason
    try:
        SubstituteReason(reason)
    except ValueError:
        valid_reasons = [r.value for r in SubstituteReason]
        return format_error_response(
            "INVALID_REASON",
            f"Invalid substitute reason: {reason}",
            {"valid_reasons": valid_reasons},
        )

    # Check agent owns the task
    assigned_to = task.get("assigned_to")
    if assigned_to:
        agent_uuid = await resolve_agent_uuid_cached(agent_id, client)
        if agent_uuid and str(assigned_to) != agent_uuid:
            return format_error_response(
                "NOT_OWNER",
                "You can only substitute out of tasks you own",
                {"task_owner": str(assigned_to), "requester": agent_id},
            )

    return None


async def _execute_substitute(
    client: ApiClient, req: SubstituteRequest
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Execute substitution. Returns (result, None) or (None, error)."""
    # Determine new status based on reason
    new_status = REASON_TO_STATUS.get(req.reason, TaskStatus.PENDING)

    # Call API to execute substitution
    resp = await client.post(
        f"/tasks/{req.task_id}/substitute",
        json={
            "agent_id": req.agent_id,
            "reason": req.reason.value,
            "details": req.details,
            "new_status": new_status.value,
            "suggested_role": req.suggested_role,
            "suggested_team": req.suggested_team,
        },
    )

    if not resp.ok:
        # If endpoint doesn't exist yet, fall back to manual status update
        if resp.status_code == HTTP_NOT_FOUND:
            # Fallback: just update status and clear assignment
            update_resp = await client.put(
                f"/tasks/{req.task_id}",
                json={
                    "status": new_status.value,
                    "assigned_to": None,  # Clear assignment
                    "dev_notes": (
                        f"[SUBSTITUTE] Reason: {req.reason.value}\n{req.details}"
                    ),
                },
            )
            if not update_resp.ok:
                return None, format_error_response(
                    "SUBSTITUTE_FAILED",
                    "Failed to execute substitution",
                    {"api_error": update_resp.text},
                )
            result: dict[str, Any] = update_resp.json()
            return result, None

        return None, format_error_response(
            "SUBSTITUTE_FAILED",
            "Failed to execute substitution",
            {"api_error": resp.text},
        )

    result = resp.json()
    return result, None


async def handle_task_substitute(
    client: ApiClient,
    task_id: str,
    agent_id: str,
    reason: str,
    details: str,
    **kwargs: str | None,
) -> dict[str, Any]:
    """Handle task substitution request.

    Allows agents to release tasks gracefully when they can't continue.
    This bypasses the normal "can't claim while in_progress" rule.

    Args:
        client: API client
        task_id: Task to release
        agent_id: Agent requesting substitution
        reason: Substitution reason (SubstituteReason enum value)
        details: Human-readable explanation
        **kwargs: Optional suggested_role and suggested_team hints

    Returns:
        Response dict with next steps
    """
    # Fetch task
    task, error = await fetch_task_or_error(client, task_id)
    if error:
        return error
    if task is None:
        raise RuntimeError("Invariant: task must be set")

    # Validate request
    if error := await _validate_substitute_request(task, agent_id, reason, client):
        return error

    # Parse reason and build request
    substitute_reason = SubstituteReason(reason)
    req = SubstituteRequest(
        task_id=task_id,
        agent_id=agent_id,
        reason=substitute_reason,
        details=details,
        suggested_role=kwargs.get("suggested_role"),
        suggested_team=kwargs.get("suggested_team"),
    )

    # Execute substitution
    result, error = await _execute_substitute(client, req)
    if error:
        return error
    if result is None:
        raise RuntimeError("Invariant: result must be set")

    # Determine next action message
    if substitute_reason == SubstituteReason.TASK_COMPLETE:
        next_action = (
            "Task released for QA review. "
            "You are now free to claim new work with roboco_task_scan()."
        )
    elif substitute_reason == SubstituteReason.BLOCKED_EXTERNAL:
        next_action = (
            "Task marked as blocked. PM will be notified. "
            "You are now free to claim new work with roboco_task_scan()."
        )
    elif substitute_reason == SubstituteReason.OUT_OF_SCOPE_ROLE:
        next_action = (
            "Task sent to PM for reassignment (role conflict). "
            "You are now free to claim new work with roboco_task_scan()."
        )
    else:
        next_action = (
            "Task released and will be reassigned. "
            "You are now free to claim new work with roboco_task_scan()."
        )

    return format_task_response(
        result,
        "RELEASED",
        f"Substitution successful ({substitute_reason.value}). {next_action}",
    )
