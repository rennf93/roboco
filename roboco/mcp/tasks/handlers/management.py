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

# Roles that cannot be assigned to cell-specific work
BOARD_ROLES = frozenset({"product_owner", "head_marketing", "auditor", "ceo"})

# Teams that represent cell work (not board/strategic)
CELL_TEAMS = frozenset({"backend", "frontend", "ux_ui"})


def validate_assignee_can_work_on_team(
    assignee: str, task_team: str | None
) -> dict[str, Any] | None:
    """Validate assignee can work on the task's team.

    Board members (product_owner, head_marketing, auditor) cannot be assigned
    to cell-specific work (backend, frontend, ux_ui tasks).

    Returns error dict or None if valid.
    """
    assignee_role = get_agent_role(assignee)
    assignee_team = get_agent_team(assignee)

    # Board members cannot work on cell tasks
    if assignee_role in BOARD_ROLES and task_team in CELL_TEAMS:
        return format_error_response(
            "INVALID_ASSIGNEE",
            f"Cannot assign {assignee_role} to {task_team} tasks. "
            "Board members handle strategic work, not cell tasks.",
            {
                "assignee": assignee,
                "assignee_role": assignee_role,
                "task_team": task_team,
                "guidance": "Assign to a cell member (e.g., be-dev-1, be-pm) instead.",
            },
        )

    # Cell members should only work on their own team's tasks
    if assignee_team and task_team and assignee_team != task_team:
        # Main PM is an exception - can work across teams
        if assignee_role == "main_pm":
            return None
        return format_error_response(
            "TEAM_MISMATCH",
            f"Cannot assign {assignee} ({assignee_team}) to {task_team} task.",
            {
                "assignee": assignee,
                "assignee_team": assignee_team,
                "task_team": task_team,
                "guidance": f"Assign to a {task_team} team member instead.",
            },
        )

    return None


_STATUS_ROLE_MAP: dict[str, str | tuple[str, ...]] = {
    "awaiting_qa": "qa",
    "awaiting_documentation": "documenter",
    "awaiting_pm_review": ("cell_pm", "main_pm"),
}

_QA_TITLE_KEYWORDS = ("qa", "test", "validation", "quality", "review")
_DOC_TITLE_KEYWORDS = ("doc", "documentation", "readme", "guide", "reference")


def _status_role_mismatch(
    task_status: str | None, assignee_role: str | None
) -> str | None:
    """Return a mismatch warning based on status, or None."""
    if task_status not in _STATUS_ROLE_MAP:
        return None
    expected = _STATUS_ROLE_MAP[task_status]
    if isinstance(expected, tuple):
        if assignee_role in expected:
            return None
        return (
            f"Task is {task_status} - typically assigned to "
            f"{' or '.join(expected)}, not {assignee_role}."
        )
    if assignee_role == expected:
        return None
    return (
        f"Task is {task_status} - typically assigned to "
        f"{expected}, not {assignee_role}."
    )


def _title_role_mismatch(task_title: str, assignee_role: str | None) -> str | None:
    """Return a mismatch warning based on title keywords, or None."""
    if any(kw in task_title for kw in _QA_TITLE_KEYWORDS) and assignee_role != "qa":
        return (
            f"Task title suggests QA work but assignee is {assignee_role}. "
            "Consider assigning to a QA agent."
        )
    if (
        any(kw in task_title for kw in _DOC_TITLE_KEYWORDS)
        and assignee_role != "documenter"
    ):
        return (
            f"Task title suggests documentation but assignee is {assignee_role}. "
            "Consider assigning to a documenter."
        )
    return None


def get_role_mismatch_warning(task: dict[str, Any], assignee: str) -> str | None:
    """Check if assignee role matches the task type and return warning if mismatch.

    Returns a warning string or None if no mismatch.
    This is a SOFT warning, not a blocking error - PM has flexibility.
    """
    assignee_role = get_agent_role(assignee)
    task_status = task.get("status")
    task_title = task.get("title", "").lower()

    if warning := _status_role_mismatch(task_status, assignee_role):
        return warning

    if task_status == "pending":
        return _title_role_mismatch(task_title, assignee_role)

    return None


def validate_cell_pm_assignment(
    role: str,
    agent_team: str | None,
    task: dict[str, Any],
    assignee: str,
) -> dict[str, Any] | None:
    """Validate Cell PM assignment restrictions. Returns error dict or None."""
    # First validate assignee can work on the team (applies to ALL roles)
    task_team = task.get("team")
    if error := validate_assignee_can_work_on_team(assignee, task_team):
        return error

    # Additional Cell PM restrictions
    if role != "cell_pm":
        return None

    if task_team != agent_team:
        return format_error_response(
            "TEAM_MISMATCH",
            f"Cell PM can only assign tasks in their team ({agent_team})",
            {"task_team": task_team},
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


async def _validate_project(
    client: ApiClient,
    input_data: TaskCreateInput,
) -> tuple[str | None, dict[str, Any] | None]:
    """Validate project for task (all tasks require project).

    Returns (project_id, None) on success, or (None, error) on failure.
    """
    # All tasks require project_slug
    if not input_data.project_slug:
        return None, format_error_response(
            "PROJECT_REQUIRED",
            "All tasks require a project. Specify project_slug.",
            {"hint": "Use roboco_project_list() to see available projects."},
        )

    # Fetch and validate project
    return await _fetch_and_validate_project(client, input_data)


async def _fetch_and_validate_project(
    client: ApiClient,
    input_data: TaskCreateInput,
) -> tuple[str | None, dict[str, Any] | None]:
    """Fetch project and validate cell match. Returns (project_id, None) or error."""
    try:
        resp = await client.get(f"/projects/{input_data.project_slug}")
    except Exception as e:
        return None, format_error_response(
            "CONNECTION_ERROR", f"Failed to validate project: {type(e).__name__}"
        )

    if resp.is_status(status.HTTP_404_NOT_FOUND):
        return None, format_error_response(
            "PROJECT_NOT_FOUND",
            f"Project '{input_data.project_slug}' not found",
            {"hint": "Use roboco_project_list() to see available projects."},
        )

    if not resp.ok:
        return None, format_error_response(
            "PROJECT_VALIDATION_FAILED",
            "Failed to validate project",
            {"status_code": resp.status_code},
        )

    project = resp.json()
    project_cell = project.get("assigned_cell")
    if project_cell and project_cell != input_data.team:
        return None, format_error_response(
            "CELL_MISMATCH",
            f"Project is {project_cell}, task is {input_data.team}",
            {"hint": "Change task team or select a project for that team."},
        )

    return project.get("id"), None


def _build_task_payload(
    input_data: TaskCreateInput,
    project_id: str | None = None,
    parent_task: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build task creation payload from input data.

    Task type logic:
    - If assigning to a PM, task_type defaults to 'planning' (PMs coordinate).
    - If assigning to a cell member, task_type stays as specified (default=code).
    - For subtasks WITHOUT an assignee, inherits task_type from parent.
    - Cell members assigned to subtasks do NOT inherit 'planning' from PM.
    """
    # Determine task_type
    task_type = input_data.task_type
    assignee_role: str | None = None

    if input_data.assigned_to:
        assignee_role = get_agent_role(input_data.assigned_to)

    # If assigning to a PM, default to 'planning' (PMs don't code)
    if assignee_role in ("cell_pm", "main_pm") and task_type == "code":
        task_type = "planning"

    # For subtasks, inherit from parent ONLY if:
    # 1. Has parent task
    # 2. task_type is still default ("code")
    # 3. NOT assigned to a cell member (they do code work, not planning)
    # This prevents developers from incorrectly getting task_type="planning"
    # when assigned to subtasks under PM coordination tasks.
    should_inherit = (
        parent_task
        and task_type == "code"
        and assignee_role not in ("developer", "qa", "documenter")
    )
    if should_inherit and parent_task is not None:
        task_type = parent_task.get("task_type", "code")

    payload: dict[str, Any] = {
        "title": input_data.title,
        "description": input_data.description,
        "acceptance_criteria": input_data.acceptance_criteria,
        "team": input_data.team,
        "priority": input_data.priority,
        "estimated_complexity": input_data.complexity,
        "nature": input_data.nature,
        "status": input_data.status,  # Always included, defaults to "backlog"
        "sequence": input_data.sequence,  # Task ordering (lower = first)
        "task_type": task_type,
        "project_id": project_id,  # Required for all tasks
    }
    if input_data.parent_task_id:
        payload["parent_task_id"] = input_data.parent_task_id
        # NOTE: Branch is auto-created on claim, not inherited at creation time.
        # Each task in the hierarchy gets its own branch forked from parent's branch.
    if input_data.dependency_ids:
        payload["dependency_ids"] = input_data.dependency_ids
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


def _validate_description_length(
    input_data: TaskCreateInput,
) -> dict[str, Any] | None:
    """Enforce a minimum description length; stricter for subtasks."""
    description = input_data.description.strip()
    is_subtask = input_data.parent_task_id is not None
    min_len = 50 if is_subtask else 30
    if len(description) >= min_len:
        return None

    if is_subtask:
        return format_error_response(
            "SUBTASK_DESCRIPTION_REQUIRED",
            f"Subtasks MUST have detailed descriptions "
            f"(min {min_len} chars, got {len(description)}). "
            "Explain what to do, why, and expected outcome.",
            {
                "parent_task_id": input_data.parent_task_id,
                "description_length": len(description),
            },
        )
    return format_error_response(
        "TASK_DESCRIPTION_REQUIRED",
        f"Tasks MUST have meaningful descriptions "
        f"(min {min_len} chars, got {len(description)}). "
        "Main PM must explain what Cell PM should accomplish.",
        {
            "description_length": len(description),
            "guidance": "Include: goal, context, and acceptance criteria.",
        },
    )


def _validate_assignee_role(
    caller_role: str | None,
    assignee: str | None,
    task_type: str,
) -> dict[str, Any] | None:
    """PMs cannot assign code tasks to other PMs (or themselves)."""
    pm_roles = ("main_pm", "cell_pm")
    if caller_role not in pm_roles or task_type != "code" or not assignee:
        return None
    assignee_role = get_agent_role(assignee)
    if assignee_role not in pm_roles:
        return None
    return format_error_response(
        "PM_CANNOT_OWN_CODE_TASKS",
        "PMs cannot be assigned code tasks. Assign to a developer.",
        {"assignee": assignee, "assignee_role": assignee_role},
        hint="Assign to: be-dev-1, be-dev-2, fe-dev-1, etc.",
    )


async def _validate_task_create_inputs(
    client: ApiClient, input_data: TaskCreateInput, agent_id: str
) -> tuple[str | None, dict[str, Any] | None]:
    """Validate all inputs for task creation. Returns (project_id, None) or error."""
    if error := _validate_create_permissions(agent_id):
        return None, error

    if error := _validate_cell_pm_team(agent_id, input_data.team):
        return None, error

    if error := _validate_description_length(input_data):
        return None, error

    assignee = input_data.assigned_to
    if assignee and (
        error := validate_assignee_can_work_on_team(assignee, input_data.team)
    ):
        return None, error

    caller_role = get_agent_role(agent_id)
    if error := _validate_assignee_role(caller_role, assignee, input_data.task_type):
        return None, error

    return await _validate_project(client, input_data)


async def _resolve_parent_task(
    client: ApiClient, parent_task_id: str | None
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Fetch parent task and enforce the claim-before-subtask rule.

    Returns (parent_task, error). parent_task is None when no parent_task_id
    is given or the fetch fails; error is non-None if the parent is not yet
    claimed.
    """
    if not parent_task_id:
        return None, None

    parent_resp = await client.get(f"/tasks/{parent_task_id}")
    if not parent_resp.ok:
        return None, None

    parent_task = parent_resp.json()
    if parent_task.get("claimed_by"):
        return parent_task, None

    return parent_task, format_error_response(
        "CLAIM_REQUIRED",
        "You must CLAIM this task before creating subtasks.",
        {
            "parent_task_id": parent_task["id"],
            "parent_status": parent_task.get("status"),
            "parent_claimed_by": parent_task.get("claimed_by"),
            "workflow": "SCAN → CLAIM → PLAN → SUBTASKS",
        },
        hint=f"Call roboco_task_claim('{parent_task['id']}') first.",
    )


async def _apply_assignment(
    client: ApiClient, task: dict[str, Any], assigned_to: str | None
) -> tuple[dict[str, Any], str | None]:
    """Apply assignment to a newly-created task. Returns (task, failure_msg)."""
    if not assigned_to:
        return task, None
    assigned_task, assign_error = await assign_task_to_agent(
        client, task["id"], assigned_to
    )
    if assigned_task:
        return assigned_task, None
    failure_msg = (
        assign_error.get("guidance", "Assignment failed") if assign_error else None
    )
    return task, failure_msg


async def handle_task_create(
    client: ApiClient, input_data: TaskCreateInput, agent_id: str
) -> dict[str, Any]:
    """Handle task creation by PM."""
    project_id, error = await _validate_task_create_inputs(client, input_data, agent_id)
    if error:
        return error

    parent_task, parent_error = await _resolve_parent_task(
        client, input_data.parent_task_id
    )
    if parent_error:
        return parent_error

    payload = _build_task_payload(input_data, project_id, parent_task)

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

    task, assignment_failure = await _apply_assignment(
        client, create_resp.json(), input_data.assigned_to
    )

    guidance = _format_create_guidance(task, input_data.assigned_to)

    if assignment_failure:
        guidance += (
            f"\n\n🚨 CRITICAL: Assignment to '{input_data.assigned_to}' FAILED! "
            f"Error: {assignment_failure}. "
            "Task was created but NOT assigned to intended agent. "
            "You may need to manually assign using roboco_task_assign()."
        )

    # Check for role mismatch and add warning if found
    if input_data.assigned_to:
        role_warning = get_role_mismatch_warning(task, input_data.assigned_to)
        if role_warning:
            guidance += f"\n\n⚠️ ROLE WARNING: {role_warning}"

    return format_task_response(task, "CREATED", guidance)


_MANAGEMENT_ROLES = frozenset(
    {"cell_pm", "main_pm", "product_owner", "head_marketing", "ceo"}
)
_COMPLEX_COMPLEXITIES = frozenset({"medium", "high", "critical"})


def _guard_self_reassign(
    task: dict[str, Any], caller_role: str, caller_uuid: str | None
) -> dict[str, Any] | None:
    """Non-management roles cannot reassign a task assigned to themselves."""
    task_assigned_to = task.get("assigned_to")
    task_assigned_str = str(task_assigned_to).lower() if task_assigned_to else None
    caller_uuid_str = str(caller_uuid).lower() if caller_uuid else None
    if not (task_assigned_str and caller_uuid_str):
        return None
    if task_assigned_str != caller_uuid_str:
        return None
    if caller_role in _MANAGEMENT_ROLES:
        return None

    task_id = task.get("id")
    return format_error_response(
        "CANNOT_REASSIGN_OWN_TASK",
        "You cannot reassign a task that was assigned to you. "
        "Create subtasks to delegate work.",
        {
            "task_id": task_id,
            "assigned_to": task_assigned_to,
            "your_id": caller_uuid,
        },
        hint=(
            f"Use roboco_task_create(parent_task_id='{task_id}', "
            "assigned_to='be-dev-1', ...) to create a subtask."
        ),
    )


async def _guard_complex_direct_dev_assignment(
    client: ApiClient,
    task: dict[str, Any],
    caller_role: str,
    assignee_role: str | None,
) -> dict[str, Any] | None:
    """Block Cell PM from directly assigning devs on complex tasks with no breakdown."""
    if caller_role != "cell_pm" or assignee_role != "developer":
        return None
    complexity = task.get("estimated_complexity", "low")
    if complexity not in _COMPLEX_COMPLEXITIES:
        return None

    task_id = task.get("id")
    try:
        subtasks_resp = await client.get(f"/tasks/{task_id}/subtasks")
        subtasks = subtasks_resp.json() if subtasks_resp.ok else []
    except Exception:
        subtasks = []

    is_subtask = task.get("parent_task_id") is not None
    if subtasks or is_subtask:
        return None

    return format_error_response(
        "SUBTASK_REQUIRED",
        f"Cannot assign {complexity} complexity task directly to dev. "
        "Cell PM must break down the work into subtasks first.",
        {
            "task_id": task_id,
            "complexity": complexity,
            "guidance": (
                f"Create subtasks with: roboco_task_create("
                f"parent_task_id='{task_id}', ...) "
                "Then assign each subtask to developers."
            ),
        },
    )


def _guard_dev_needs_branch(
    task: dict[str, Any], assignee_role: str | None
) -> dict[str, Any] | None:
    """Tasks need a branch before assigning to developers."""
    if task.get("branch_name") or assignee_role != "developer":
        return None
    return format_error_response(
        "NO_BRANCH_FOR_TASK",
        "Task must have a branch before assigning to developer.",
        {
            "task_id": task.get("id"),
            "has_branch": False,
        },
        hint=(
            "Either claim the task first (creates branch), "
            "or create subtasks for developers."
        ),
    )


async def _check_assignment_guardrails(
    client: ApiClient,
    task: dict[str, Any],
    roles: tuple[str, str],
    caller_uuid: str | None = None,
) -> dict[str, Any] | None:
    """Check guardrails for task assignment. Returns error or None.

    Args:
        client: API client
        task: Task dict with id, assigned_to, etc.
        roles: Tuple of (caller_role, assignee_role)
        caller_uuid: UUID of the caller for ownership check
    """
    caller_role, assignee_role = roles

    if error := _guard_self_reassign(task, caller_role, caller_uuid):
        return error

    if error := await _guard_complex_direct_dev_assignment(
        client, task, caller_role, assignee_role
    ):
        return error

    return _guard_dev_needs_branch(task, assignee_role)


async def _validate_and_fetch_task_for_assign(
    client: ApiClient, input_data: TaskAssignInput, agent_id: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Run permission + cell PM checks and fetch the task. Returns (task, error)."""
    role = get_agent_role(agent_id)
    if not can_assign_tasks(agent_id):
        return None, format_error_response(
            "PERMISSION_DENIED",
            "Only PMs and management can assign tasks",
            {"role": role},
        )

    task, error = await fetch_task_for_assignment(client, input_data.task_id)
    if error or task is None:
        return None, error or format_error_response("FETCH_FAILED", "No task returned")

    agent_team = get_agent_team(agent_id)
    validation_error = validate_cell_pm_assignment(
        role, agent_team, task, input_data.assignee
    )
    if validation_error:
        return None, validation_error
    return task, None


def _build_assign_guidance(task: dict[str, Any], assignee: str) -> str:
    """Build response guidance text for a successful assignment."""
    guidance = (
        f"Task assigned to {assignee} and set to pending. "
        "Orchestrator will spawn them to claim and work on it."
    )
    role_warning = get_role_mismatch_warning(task, assignee)
    if role_warning:
        guidance += f"\n\n⚠️ ROLE WARNING: {role_warning}"
    if task.get("status") == "claimed" and task.get("claimed_by"):
        guidance += (
            "\n\n⚠️ Note: This task was already claimed. If you're delegating work, "
            "consider using roboco_task_create(parent_task_id=...) to create a subtask "
            "for better tracking and branch hierarchy management."
        )
    return guidance


async def handle_task_assign(
    client: ApiClient, input_data: TaskAssignInput, agent_id: str
) -> dict[str, Any]:
    """Handle task assignment by PM."""
    task, error = await _validate_and_fetch_task_for_assign(
        client, input_data, agent_id
    )
    if error or task is None:
        return error or format_error_response("FETCH_FAILED", "No task returned")

    caller_uuid = await resolve_agent_uuid_cached(agent_id, client)
    assignee_role = get_agent_role(input_data.assignee)
    caller_role = get_agent_role(agent_id)
    guardrail_error = await _check_assignment_guardrails(
        client, task, (caller_role, assignee_role), caller_uuid
    )
    if guardrail_error:
        return guardrail_error

    assigned_task, assign_error = await assign_task_to_agent(
        client, input_data.task_id, input_data.assignee
    )
    if assign_error or assigned_task is None:
        return assign_error or format_error_response("ASSIGN_FAILED", "No task")

    guidance = _build_assign_guidance(task, input_data.assignee)
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
    client: ApiClient, input_data: TaskEscalateInput, _agent_id: str
) -> dict[str, Any]:
    """Handle task escalation up the hierarchy.

    Uses the dedicated /tasks/{task_id}/escalate endpoint which bypasses
    normal notification permission checks. All agents can escalate.

    Note: _agent_id is unused since the API endpoint uses its own auth context.
    Keeping for consistent function signature with other handlers.
    """
    try:
        # Use the dedicated escalate endpoint (bypasses notification permissions)
        escalate_data = {
            "reason": input_data.reason,
        }
        if input_data.escalate_to:
            escalate_data["escalate_to"] = input_data.escalate_to

        resp = await client.post(
            f"/tasks/{input_data.task_id}/escalate", json=escalate_data
        )

        if resp.is_status(status.HTTP_404_NOT_FOUND):
            return format_error_response(
                "NOT_FOUND", f"Task {input_data.task_id} not found"
            )

        if not resp.ok:
            error_detail = resp.text
            try:
                error_json = resp.json()
                error_detail = error_json.get("detail", resp.text)
            except Exception:
                pass
            return format_error_response(
                "ESCALATION_FAILED",
                f"Failed to escalate task: {error_detail}",
                {"status_code": resp.status_code},
            )

        result = resp.json()

        # Get task for response formatting
        task_resp = await client.get(f"/tasks/{input_data.task_id}")
        task = task_resp.json() if task_resp.ok else {}

        guidance = result.get(
            "message",
            f"Task escalated to {result.get('escalated_to', 'PM')}. "
            "They will be notified and can reassign or provide guidance.",
        )
        return format_task_response(task, "ESCALATED", guidance)

    except Exception as e:
        return format_error_response(
            "CONNECTION_ERROR",
            f"Failed to connect to API: {type(e).__name__}",
        )


async def _validate_activation_sequence(
    client: ApiClient, task_id: str
) -> dict[str, Any] | None:
    """Ensure parent is in_progress before activating subtask."""
    task_resp = await client.get(f"/tasks/{task_id}")
    if not task_resp.ok:
        return None

    task = task_resp.json()
    parent_id = task.get("parent_task_id")
    if not parent_id:
        return None  # Root task

    parent_resp = await client.get(f"/tasks/{parent_id}")
    if not parent_resp.ok:
        return None

    parent = parent_resp.json()
    parent_status = parent.get("status")

    # Parent must be in_progress or paused (PM has started work)
    if parent_status not in ["in_progress", "paused"]:
        return format_error_response(
            "PARENT_NOT_STARTED",
            f"Cannot activate - parent task is '{parent_status}'.",
            {"parent_id": parent_id, "parent_status": parent_status},
            hint="Call roboco_task_start() on parent task first.",
        )

    return None


def _handle_activate_response(resp: Any, task_id: str) -> dict[str, Any] | None:
    """Handle API response for activation. Returns error dict or None if success."""
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

    return None


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

    # Validate parent is started before activating subtask
    if error := await _validate_activation_sequence(client, task_id):
        return error

    try:
        resp = await client.post(f"/tasks/{task_id}/activate")
    except Exception as e:
        return format_error_response(
            "CONNECTION_ERROR",
            f"Failed to connect to API: {type(e).__name__}",
        )

    if error := _handle_activate_response(resp, task_id):
        return error

    task = resp.json()
    guidance = (
        "Task activated. Status is now PENDING. "
        "Orchestrator will spawn agents to work on it."
    )
    return format_task_response(task, "ACTIVATED", guidance)
