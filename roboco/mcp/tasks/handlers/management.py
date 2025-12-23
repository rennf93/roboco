"""
Task MCP Server PM Handlers

PM-specific task handlers for the Task MCP server.
Includes task creation, assignment, and escalation.
"""

from typing import Any

from fastapi import status

from roboco.agents_config import (
    can_assign_tasks,
    can_create_tasks,
    get_agent_role,
    get_agent_team,
    get_escalation_target,
)
from roboco.mcp.schemas import TaskAssignInput, TaskCreateInput, TaskEscalateInput
from roboco.mcp.tasks import format_task_response
from roboco.mcp.utils import ApiClient, format_error_response, resolve_agent_uuid_cached

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def validate_cell_pm_assignment(
    role: str,
    agent_team: str | None,
    task: dict[str, Any],
    assignee: str,
) -> dict[str, Any] | None:
    """Validate Cell PM assignment restrictions. Returns error dict or None."""
    if role != "cell_pm":
        return None

    task_team = task.get("team")
    if task_team != agent_team:
        return format_error_response(
            "TEAM_MISMATCH",
            f"Cell PM can only assign tasks in their team ({agent_team})",
            {"task_team": task_team},
        )

    assignee_team = get_agent_team(assignee)
    if assignee_team and assignee_team != agent_team:
        return format_error_response(
            "ASSIGNEE_MISMATCH",
            "Cannot assign to agent outside your team",
            {"assignee_team": assignee_team, "your_team": agent_team},
        )

    return None


async def fetch_task_for_assignment(
    client: ApiClient, task_id: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Fetch task for assignment. Returns (task, error) tuple."""
    try:
        task_resp = await client.get(f"/tasks/{task_id}")
    except Exception as e:
        return None, format_error_response(
            "CONNECTION_ERROR",
            f"Failed to connect to API: {type(e).__name__}",
        )

    if task_resp.is_status(status.HTTP_404_NOT_FOUND):
        return None, format_error_response("NOT_FOUND", f"Task {task_id} not found")

    if not task_resp.ok:
        return None, format_error_response(
            "FETCH_FAILED",
            "Failed to fetch task",
            {"status_code": task_resp.status_code},
        )

    return task_resp.json(), None


async def assign_task_to_agent(
    client: ApiClient, task_id: str, assignee: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """
    Assign task to agent by setting assigned_to and resetting to pending.

    Returns (assigned_task, error) tuple.
    """
    assignee_id = await resolve_agent_uuid_cached(assignee, client)
    if not assignee_id:
        return None, format_error_response(
            "INVALID_ASSIGNEE",
            f"Could not resolve agent: {assignee}",
            {"assignee": assignee},
        )

    try:
        assign_resp = await client.patch(
            f"/tasks/{task_id}",
            json={"assigned_to": assignee_id, "status": "pending"},
        )
    except Exception as e:
        return None, format_error_response(
            "CONNECTION_ERROR",
            f"Failed to connect to API: {type(e).__name__}",
        )

    if not assign_resp.ok:
        return None, format_error_response(
            "ASSIGN_FAILED",
            "Failed to assign task",
            {"status_code": assign_resp.status_code, "detail": assign_resp.text},
        )

    return assign_resp.json(), None


# =============================================================================
# PM HANDLERS
# =============================================================================


def _validate_create_permissions(agent_id: str) -> dict[str, Any] | None:
    """Validate agent can create tasks. Returns error or None."""
    if not can_create_tasks(agent_id):
        return format_error_response(
            "PERMISSION_DENIED",
            "Only PMs and management can create tasks",
            {"role": get_agent_role(agent_id)},
        )
    return None


def _validate_cell_pm_team(agent_id: str, requested_team: str) -> dict[str, Any] | None:
    """Validate Cell PM team restrictions for task creation. Returns error or None."""
    role = get_agent_role(agent_id)
    agent_team = get_agent_team(agent_id)
    if role == "cell_pm" and requested_team != agent_team:
        return format_error_response(
            "TEAM_MISMATCH",
            f"Cell PM can only create tasks for their team ({agent_team})",
            {"requested_team": requested_team, "agent_team": agent_team},
        )
    return None


def _build_task_payload(input_data: TaskCreateInput) -> dict[str, Any]:
    """Build task creation payload from input data."""
    payload: dict[str, Any] = {
        "title": input_data.title,
        "description": input_data.description,
        "acceptance_criteria": input_data.acceptance_criteria,
        "team": input_data.team,
        "priority": input_data.priority,
        "estimated_complexity": input_data.complexity,
        "status": input_data.status,  # Always included, defaults to "backlog"
    }
    if input_data.parent_task_id:
        payload["parent_task_id"] = input_data.parent_task_id
    return payload


def _format_create_guidance(task: dict[str, Any], assigned_to: str | None) -> str:
    """Format guidance message for task creation."""
    task_status = task.get("status", "pending")
    guidance = f"Task created successfully. ID: {task['id']}. Status: {task_status}. "

    if task_status == "backlog":
        guidance += (
            "Task is in BACKLOG. Create session with "
            "roboco_session_create_for_tasks, then roboco_task_activate."
        )
    elif assigned_to:
        guidance += (
            f"Assigned to: {assigned_to}. "
            "Orchestrator will spawn them to claim and work on it."
        )
    else:
        guidance += "Task is pending - assign it or let orchestrator route it."
    return guidance


async def handle_task_create(
    client: ApiClient, input_data: TaskCreateInput, agent_id: str
) -> dict[str, Any]:
    """Handle task creation by PM."""
    if error := _validate_create_permissions(agent_id):
        return error

    if error := _validate_cell_pm_team(agent_id, input_data.team):
        return error

    payload = _build_task_payload(input_data)

    try:
        create_resp = await client.post("/tasks", json=payload)
    except Exception as e:
        return format_error_response(
            "CONNECTION_ERROR",
            f"Failed to connect to API: {type(e).__name__}",
        )

    if not create_resp.is_status(status.HTTP_201_CREATED):
        return format_error_response(
            "CREATE_FAILED",
            "Failed to create task",
            {"status_code": create_resp.status_code, "detail": create_resp.text},
        )

    task = create_resp.json()

    if input_data.assigned_to:
        assigned_task, _ = await assign_task_to_agent(
            client, task["id"], input_data.assigned_to
        )
        if assigned_task:
            task = assigned_task

    guidance = _format_create_guidance(task, input_data.assigned_to)
    return format_task_response(task, "CREATED", guidance)


async def handle_task_assign(
    client: ApiClient, input_data: TaskAssignInput, agent_id: str
) -> dict[str, Any]:
    """Handle task assignment by PM."""
    agent_team = get_agent_team(agent_id)
    role = get_agent_role(agent_id)

    if not can_assign_tasks(agent_id):
        return format_error_response(
            "PERMISSION_DENIED",
            "Only PMs and management can assign tasks",
            {"role": role},
        )

    task, error = await fetch_task_for_assignment(client, input_data.task_id)
    if error or task is None:
        return error or format_error_response("FETCH_FAILED", "No task returned")

    validation_error = validate_cell_pm_assignment(
        role, agent_team, task, input_data.assignee
    )
    if validation_error:
        return validation_error

    assigned_task, assign_error = await assign_task_to_agent(
        client, input_data.task_id, input_data.assignee
    )
    if assign_error or assigned_task is None:
        return assign_error or format_error_response("ASSIGN_FAILED", "No task")

    guidance = (
        f"Task assigned to {input_data.assignee} and set to pending. "
        "Orchestrator will spawn them to claim and work on it."
    )
    return format_task_response(assigned_task, "ASSIGNED", guidance)


def _get_escalation_target(
    agent_id: str, explicit_target: str | None
) -> tuple[str | None, dict[str, Any] | None]:
    """Get escalation target. Returns (target, None) or (None, error)."""
    target = explicit_target or get_escalation_target(agent_id)
    if not target:
        return None, format_error_response(
            "NO_ESCALATION_PATH",
            f"No escalation path from agent: {agent_id}",
            {"role": get_agent_role(agent_id)},
        )
    return target, None


def _build_escalation_notification(
    task: dict[str, Any], task_id: str, agent_id: str, reason: str, target_uuid: str
) -> dict[str, Any]:
    """Build escalation notification payload."""
    return {
        "type": "blocker_escalation",
        "to_agents": [target_uuid],
        "subject": f"Escalation: {task.get('title', 'Unknown task')}",
        "body": f"Task {task_id} escalated by {agent_id}.\n\nReason: {reason}",
        "related_task_id": task_id,
        "priority": "high",
    }


async def handle_task_escalate(
    client: ApiClient, input_data: TaskEscalateInput, agent_id: str
) -> dict[str, Any]:
    """Handle task escalation up the hierarchy."""
    target, error = _get_escalation_target(agent_id, input_data.escalate_to)
    if error:
        return error
    assert target is not None

    target_uuid = await resolve_agent_uuid_cached(target, client)
    if not target_uuid:
        return format_error_response(
            "INVALID_TARGET",
            f"Could not resolve escalation target: {target}",
        )

    try:
        task_resp = await client.get(f"/tasks/{input_data.task_id}")
        if task_resp.is_status(status.HTTP_404_NOT_FOUND):
            return format_error_response(
                "NOT_FOUND", f"Task {input_data.task_id} not found"
            )
        task = task_resp.json()

        notification = _build_escalation_notification(
            task, input_data.task_id, agent_id, input_data.reason, target_uuid
        )
        notif_resp = await client.post("/notifications", json=notification)

        if not notif_resp.ok and not notif_resp.is_status(status.HTTP_201_CREATED):
            return format_error_response(
                "ESCALATION_FAILED",
                "Failed to send escalation notification",
                {"status_code": notif_resp.status_code, "detail": notif_resp.text},
            )
    except Exception as e:
        return format_error_response(
            "CONNECTION_ERROR",
            f"Failed to connect to API: {type(e).__name__}",
        )

    guidance = (
        f"Task escalated to {target}. Reason: {input_data.reason}. "
        "They will be notified and can reassign or provide guidance."
    )
    return format_task_response(task, "ESCALATED", guidance)


async def handle_task_activate(
    client: ApiClient, task_id: str, agent_id: str
) -> dict[str, Any]:
    """
    Handle task activation from BACKLOG to PENDING (PM only).

    This is the final step in PM setup. After creating a session and
    linking the task, the PM activates it to make it ready for work.
    The orchestrator will then spawn agents to claim and work on it.

    REQUIRES: Task must have at least one linked session.
    """
    if not can_create_tasks(agent_id):
        return format_error_response(
            "PERMISSION_DENIED",
            "Only PMs and management can activate tasks",
            {"role": get_agent_role(agent_id)},
        )

    try:
        resp = await client.post(f"/tasks/{task_id}/activate")
    except Exception as e:
        return format_error_response(
            "CONNECTION_ERROR",
            f"Failed to connect to API: {type(e).__name__}",
        )

    if resp.is_status(status.HTTP_404_NOT_FOUND):
        return format_error_response("NOT_FOUND", f"Task {task_id} not found")

    if resp.is_status(status.HTTP_400_BAD_REQUEST):
        detail = resp.json().get("detail", "Activation failed")
        return format_error_response("ACTIVATION_FAILED", detail)

    if not resp.ok:
        return format_error_response(
            "ACTIVATION_FAILED",
            "Failed to activate task",
            {"status_code": resp.status_code, "detail": resp.text},
        )

    task = resp.json()
    guidance = (
        "Task activated. Status is now PENDING. "
        "Orchestrator will spawn agents to work on it."
    )
    return format_task_response(task, "ACTIVATED", guidance)
