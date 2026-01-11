"""
Task Handler Helpers

Shared validation and utility functions for task handlers.
"""

from typing import Any
from uuid import uuid4

from roboco.mcp.tasks import get_available_tasks_guidance
from roboco.mcp.utils import ApiClient, format_error_response, resolve_agent_uuid_cached


async def get_available_tasks_for_role(
    client: ApiClient, agent_role: str, team: str | None
) -> list[dict[str, Any]]:
    """Get available tasks based on agent role."""
    params = {"team": team} if team else {}

    if agent_role == "qa":
        resp = await client.get("/tasks/awaiting-qa", params=params)
        return resp.json() if resp.ok else []

    if agent_role == "documenter":
        resp = await client.get("/tasks/awaiting-docs", params=params)
        return resp.json() if resp.ok else []

    if agent_role in ("cell_pm", "main_pm"):
        pending_params = {**params, "status": "pending"}
        pending_resp = await client.get("/tasks", params=pending_params)
        pending = pending_resp.json() if pending_resp.ok else []
        review_resp = await client.get(
            "/tasks", params={**params, "status": "awaiting_pm_review"}
        )
        review = review_resp.json() if review_resp.ok else []
        return pending + review

    resp = await client.get("/tasks", params={**params, "status": "pending"})
    return resp.json() if resp.ok else []


def _get_role_pending_task_guidance(
    assigned_tasks: list[dict], agent_role: str
) -> str | None:
    """Get special guidance for non-standard pending tasks.

    When QA/Documenter is directly assigned a pending task (not their usual queue),
    they need to know it's direct work - use submit_pm_review workflow.
    """
    pending_tasks = [t for t in assigned_tasks if t.get("status") == "pending"]
    if not pending_tasks:
        return None

    role_hints = {
        "qa": "These are NOT QA reviews",
        "documenter": "These are NOT awaiting_documentation tasks",
    }
    hint = role_hints.get(agent_role, "These are direct assignments")

    return (
        f"You have {len(pending_tasks)} PENDING task(s) directly assigned to you. "
        f"{hint} - they are tasks assigned for YOU to complete. "
        "Workflow: claim → plan → start → work → submit_pm_review. "
        "Use roboco_task_get to see details, then roboco_task_claim to start."
    )


def get_scan_guidance(
    paused_tasks: list[dict],
    assigned_tasks: list[dict],
    available_tasks: list[dict],
    agent_role: str | None,
) -> str:
    """Get guidance message based on task scan results."""
    if paused_tasks:
        return (
            f"You have {len(paused_tasks)} paused task(s). "
            "Resume your paused work before claiming new tasks."
        )

    # Special case: QA/Documenter with pending tasks (not their usual queue)
    if agent_role in ("qa", "documenter"):
        pending_guidance = _get_role_pending_task_guidance(assigned_tasks, agent_role)
        if pending_guidance:
            return pending_guidance

    if assigned_tasks:
        return (
            f"You have {len(assigned_tasks)} active task(s). "
            "Continue working on your assigned tasks."
        )
    if available_tasks:
        return get_available_tasks_guidance(available_tasks, agent_role or "unknown")
    return (
        "No tasks available. Call roboco_agent_idle() "
        "to signal availability, or check back later."
    )


def check_blocking_tasks(active_tasks: list[dict]) -> dict[str, Any] | None:
    """Check for blocking active tasks. Returns error or None.

    NOTE: "pending" is NOT blocking. If PM assigned multiple pending tasks,
    agent should be able to claim any of them. Only tasks being actively
    worked on (claimed, in_progress, verifying) block new claims.
    """
    blocking_statuses = ["claimed", "in_progress", "verifying"]
    blocking = [t for t in active_tasks if t.get("status") in blocking_statuses]
    if blocking:
        status = blocking[0].get("status", "active")
        return format_error_response(
            "ALREADY_ACTIVE",
            f"You have a {status} task: {blocking[0]['id']}. "
            "Work on it first, or pause it if blocked.",
            {"active_task_id": blocking[0]["id"], "status": status},
            hint="roboco_kb_search('task pause blocked')",
        )
    return None


def check_paused_tasks(active_tasks: list[dict]) -> dict[str, Any] | None:
    """Check for paused tasks. Returns error or None."""
    paused = [t for t in active_tasks if t.get("status") == "paused"]
    if paused:
        return format_error_response(
            "PAUSED_TASKS_EXIST",
            f"You have {len(paused)} paused task(s). "
            "Resume paused work before claiming new tasks.",
            {"paused_task_ids": [t["id"] for t in paused]},
        )
    return None


async def validate_task_claimable(
    task: dict, agent_role: str, agent_id: str, client: ApiClient
) -> dict[str, Any] | None:
    """Validate task can be claimed based on agent role.

    Special case: If an agent is already assigned to a pending task (PM assigned
    it directly to them), they can claim it to transition to 'claimed' status.
    """
    # ENFORCEMENT: Main PM must delegate code tasks, not execute them
    task_type = task.get("task_type", "code")
    if agent_role == "main_pm" and task_type == "code":
        return format_error_response(
            "PM_CANNOT_EXECUTE_CODE",
            "Main PM cannot claim code tasks. You coordinate, not execute.",
            {"task_type": task_type, "your_role": agent_role},
            hint=(
                "Create a subtask for the appropriate Cell PM (be-pm, fe-pm, ux-pm) "
                "using roboco_task_create(parent_task_id=this_task_id, team='backend', "
                "assigned_to='be-pm'). Then activate it with roboco_task_activate()."
            ),
        )

    task_status = task.get("status")
    claimable_statuses = {
        # QA: pending (direct QA tasks from PM) or awaiting_qa (normal workflow)
        "qa": ["pending", "awaiting_qa"],
        # Documenters: pending (direct docs tasks) or awaiting_documentation (workflow)
        "documenter": ["pending", "awaiting_documentation"],
        # Developers: pending or needs_revision (after QA/CEO rejection)
        "developer": ["pending", "needs_revision"],
        # PMs: pending or awaiting_pm_review
        "cell_pm": ["pending", "awaiting_pm_review"],
        "main_pm": ["pending", "awaiting_pm_review"],
    }
    # Default for unlisted roles is pending + needs_revision (developer-like behavior)
    allowed = claimable_statuses.get(agent_role, ["pending", "needs_revision"])

    # Special case: agent can claim tasks already assigned to them
    # This handles PM directly assigning tasks or needs_revision tasks
    if task_status in ("pending", "needs_revision"):
        assigned_to = task.get("assigned_to")
        if assigned_to:
            agent_uuid = await resolve_agent_uuid_cached(agent_id, client)
            if agent_uuid and assigned_to == agent_uuid:
                return None  # Allow claiming - already assigned to this agent

    if task_status not in allowed:
        return format_error_response(
            "INVALID_STATE",
            f"Cannot claim task in '{task_status}' status. "
            f"Your role ({agent_role}) can claim: {', '.join(allowed)}.",
            {"current_status": task_status, "allowed_statuses": allowed},
            hint="roboco_kb_search('task claim role')",
        )
    return None


async def get_project_context(
    client: ApiClient, project_id: str
) -> dict[str, Any] | None:
    """Fetch project context if available."""
    resp = await client.get(f"/projects/{project_id}")
    if resp.ok:
        result: dict[str, Any] = resp.json()
        return result
    return None


async def fetch_task_or_error(
    client: ApiClient, task_id: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Fetch task by ID, returning (task, None) or (None, error_response)."""
    from fastapi import status

    resp = await client.get(f"/tasks/{task_id}")
    if resp.is_status(status.HTTP_404_NOT_FOUND):
        return None, format_error_response("NOT_FOUND", f"Task {task_id} not found")
    task: dict[str, Any] = resp.json()
    return task, None


def validate_task_status(
    task: dict[str, Any], expected: str, action_desc: str
) -> dict[str, Any] | None:
    """Validate task is in expected status. Returns error or None."""
    if task.get("status") != expected:
        return format_error_response(
            "INVALID_STATE",
            f"Can only {action_desc} tasks in '{expected}' status. "
            f"Current: '{task.get('status')}'",
        )
    return None


def validate_task_status_in(
    task: dict[str, Any], allowed: set[str], action_desc: str
) -> dict[str, Any] | None:
    """Validate task is in one of the allowed statuses. Returns error or None.

    Use this for workflow validations where multiple statuses are valid entry points.
    Example: QA can pass tasks in awaiting_qa, claimed, or in_progress status.
    """
    task_status = task.get("status")
    if task_status not in allowed:
        return format_error_response(
            "INVALID_STATE",
            f"Can only {action_desc} tasks in {', '.join(sorted(allowed))} status. "
            f"Current: '{task_status}'",
        )
    return None


async def validate_task_ownership(
    task: dict, agent_id: str, client: ApiClient
) -> dict[str, Any] | None:
    """Validate agent owns the task."""
    assigned_to = task.get("assigned_to")
    if not assigned_to:
        return format_error_response("NOT_ASSIGNED", "Task is not assigned to anyone")

    agent_uuid = await resolve_agent_uuid_cached(agent_id, client)
    if not agent_uuid:
        return format_error_response(
            "AGENT_NOT_FOUND", f"Could not resolve agent: {agent_id}"
        )

    if str(assigned_to) != agent_uuid:
        return format_error_response(
            "NOT_OWNER",
            "You are not assigned to this task",
            {"assigned_to": assigned_to},
            hint="roboco_kb_search('task assignment ownership')",
        )
    return None


def validate_task_status_claimed(task: dict) -> dict[str, Any] | None:
    """Validate task is in claimed status."""
    if task.get("status") != "claimed":
        return format_error_response(
            "INVALID_STATE",
            f"Cannot submit plan for task in '{task.get('status')}' status. "
            "Task must be 'claimed'.",
            {"current_status": task.get("status")},
        )
    return None


def build_plan_data(plan_params: dict[str, Any]) -> dict[str, Any]:
    """Build the plan data structure from params.

    Supports two formats for open_questions:
    - List of strings: ["Question 1", "Question 2"] -> sets answered=False
    - List of dicts: [{"question": "Q1", "answered": True}] -> preserves status
    """
    raw_questions = plan_params.get("open_questions") or []
    open_questions = []
    for q in raw_questions:
        if isinstance(q, str):
            # Simple string format - new unanswered question
            open_questions.append({"question": q, "answered": False})
        elif isinstance(q, dict):
            # Dict format - preserve answered status if provided
            open_questions.append(
                {
                    "question": q.get("question", ""),
                    "answered": q.get("answered", False),
                    "answer": q.get("answer"),  # Optional: store the answer text
                }
            )

    return {
        "approach": plan_params["approach"],
        "sub_tasks": [
            {
                "id": st.get("id") or str(uuid4()),
                "title": st.get("title", ""),
                "description": st.get("description", ""),
                "order": i,
            }
            for i, st in enumerate(plan_params["sub_tasks"])
        ],
        "risks": [{"description": r} for r in (plan_params.get("risks") or [])],
        "open_questions": open_questions,
    }


async def validate_task_start(
    task: dict[str, Any], agent_id: str, client: ApiClient
) -> dict[str, Any] | None:
    """Validate task can be started."""
    if error := await validate_task_ownership(task, agent_id, client):
        return error

    task_status = task.get("status")
    valid_start_statuses = ["claimed", "paused", "needs_revision"]
    if task_status not in valid_start_statuses:
        return format_error_response(
            "INVALID_STATE",
            f"Cannot start task in '{task_status}' status. "
            "Task must be 'claimed', 'paused', or 'needs_revision'.",
            {"current_status": task_status},
        )

    if task_status == "claimed" and not task.get("plan"):
        task_id = task.get("id", "unknown")
        return format_error_response(
            "NO_PLAN",
            "Cannot start without a plan. Workflow: CLAIM → PLAN → START",
            {
                "current_status": "claimed",
                "next_action": "roboco_task_plan",
                "workflow": ["claimed", "plan submitted", "start", "in_progress"],
                "why": (
                    "Planning ensures you understand the task, break it into steps, "
                    "identify risks, and ask questions before writing code."
                ),
                "example": {
                    "tool": "roboco_task_plan",
                    "args": {
                        "task_id": task_id,
                        "approach": "Describe your high-level implementation strategy",
                        "sub_tasks": [
                            {"title": "Step 1", "description": "First action to take"},
                            {"title": "Step 2", "description": "Second action to take"},
                        ],
                        "risks": ["Potential issue that could block progress"],
                        "open_questions": ["Question to clarify with PM if any"],
                    },
                },
            },
            hint="roboco_kb_search('task plan workflow')",
        )

    if task_status == "claimed":
        plan = task.get("plan", {})
        unanswered = [
            q for q in plan.get("open_questions", []) if not q.get("answered")
        ]
        if unanswered:
            return format_error_response(
                "UNANSWERED_QUESTIONS",
                f"Cannot start with {len(unanswered)} unanswered question(s). "
                "Get answers first, then update the plan.",
                {"questions": [q.get("question") for q in unanswered]},
            )

    return None
