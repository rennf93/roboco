"""
Task MCP Server

Exposes task management tools to Claude Code agents with built-in
enforcement of task lifecycle rules.

Tools:
- roboco_task_scan: List available tasks (paused, assigned, available)
- roboco_task_get: Get task details
- roboco_task_claim: Claim a task
- roboco_task_plan: Submit implementation plan
- roboco_task_start: Start working on task
- roboco_task_progress: Update progress
- roboco_task_block: Mark task as blocked
- roboco_task_unblock: Unblock task
- roboco_task_pause: Pause task
- roboco_task_submit_qa: Submit for QA review
- roboco_task_qa_pass: Pass QA (QA role only)
- roboco_task_qa_fail: Fail QA (QA role only)
- roboco_task_docs_complete: Mark docs complete (Documenter only)
- roboco_task_complete: Mark task complete (PM only, after docs)
- roboco_task_create: Create new task (PM only)
- roboco_task_assign: Assign task to agent (PM only)
- roboco_task_cancel: Cancel a task (PM/Board only)
- roboco_task_escalate: Escalate task up hierarchy (all agents)
"""

from typing import Any

from fastapi import status
from mcp.server.fastmcp import FastMCP

from roboco.agents_config import (
    can_assign_tasks,
    can_cancel_tasks,
    can_create_tasks,
    get_agent_role,
    get_agent_team,
    get_escalation_target,
)
from roboco.llm import ToonAdapter
from roboco.mcp.schemas import (
    TaskAssignInput,
    TaskBlockInput,
    TaskCreateInput,
    TaskEscalateInput,
    TaskPauseInput,
)
from roboco.mcp.utils import (
    ApiClient,
    format_error_response,
    resolve_agent_uuid,
)
from roboco.services.task import extract_original_developer

# Alias for backwards compatibility
_format_error_response = format_error_response

# Cache for agent slug -> UUID resolution
_agent_uuid_cache: dict[str, str] = {}


async def _resolve_agent_uuid_cached(agent_id: str, client: ApiClient) -> str | None:
    """Resolve agent slug to UUID with caching. Returns None if not found."""
    if agent_id in _agent_uuid_cache:
        return _agent_uuid_cache[agent_id]
    result = await resolve_agent_uuid(agent_id, client._get_headers())
    if result:
        _agent_uuid_cache[agent_id] = result
    return result


# Global TOON adapter for encoding task data
_toon = ToonAdapter()

# Progress percentage bounds
_MIN_PERCENTAGE = 0
_MAX_PERCENTAGE = 100

# NOTE: For task lifecycle validation, use enforcement.task_lifecycle.VALID_TRANSITIONS


def _format_task_response(
    task: dict[str, Any],
    next_step: str,
    guidance: str,
    project: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Format a standardized task response with guidance.

    Includes both JSON task data and TOON-encoded version for
    token-efficient LLM consumption.
    """
    # Encode task data as TOON for token efficiency when LLM processes response
    task_toon = _toon.encode(task)

    response = {
        "status": task.get("status"),
        "task": task,
        "task_toon": task_toon,  # TOON-encoded for LLM token efficiency
        "next_step": next_step,
        "guidance": guidance,
    }
    if project:
        response["project"] = project
        response["project_toon"] = _toon.encode(project)
    return response


def _get_next_step_guidance(status: str) -> tuple[str, str]:
    """Get next step and guidance based on task status."""
    guidance_map = {
        "claimed": (
            "UNDERSTAND",
            "Read the task description and acceptance criteria carefully. "
            "If anything is unclear, ask questions before proceeding. "
            "Do NOT start coding until you fully understand the requirements.",
        ),
        "in_progress": (
            "EXECUTE",
            "Work through your plan step by step. "
            "Commit frequently with clear messages. "
            "Update progress regularly. "
            "If blocked, call roboco_task_block with details.",
        ),
        "blocked": (
            "WAIT_OR_SWITCH",
            "You are blocked. Options: "
            "1) Wait for resolution (if expected soon), "
            "2) Switch to another task (call roboco_task_scan), "
            "3) Escalate to PM if urgent.",
        ),
        "paused": (
            "RESUME_OR_SCAN",
            "This task is paused. "
            "Call roboco_task_start to resume, or "
            "call roboco_task_scan for other work.",
        ),
        "verifying": (
            "SELF_VERIFY",
            "Verify against ALL acceptance criteria. "
            "Run tests, check edge cases. "
            "If all pass, call roboco_task_submit_qa. "
            "If issues found, fix them first.",
        ),
        "awaiting_qa": (
            "WAIT_FOR_QA",
            "Task submitted for QA review. "
            "You will be notified of the result. "
            "In the meantime, scan for other available work.",
        ),
        "needs_revision": (
            "FIX_ISSUES",
            "QA found issues. Read the QA notes carefully. "
            "Fix all issues, then re-submit for QA.",
        ),
        "awaiting_documentation": (
            "DOCUMENT",
            "QA passed. Create handoff documentation for the documenter. "
            "Include: what was built, how it works, any gotchas.",
        ),
        "completed": (
            "DONE",
            "Task completed. Call roboco_task_scan for new work.",
        ),
    }
    return guidance_map.get(status, ("UNKNOWN", "Check task status."))


# =============================================================================
# TOOL IMPLEMENTATIONS
# =============================================================================


def _get_available_tasks_guidance(
    available_tasks: list[dict[str, Any]], agent_role: str
) -> str:
    """Generate guidance for available tasks based on agent role."""
    review_count = sum(
        1 for t in available_tasks if t.get("status") == "awaiting_pm_review"
    )
    pending_count = len(available_tasks) - review_count

    if agent_role in ("cell_pm", "main_pm") and review_count > 0:
        return (
            f"Found {review_count} task(s) awaiting your review. "
            "Use roboco_task_get to review, then roboco_task_complete to finalize. "
            f"Also {pending_count} pending task(s) need triage."
        )
    return (
        f"Found {len(available_tasks)} available task(s). "
        "Review and claim one that matches your skills."
    )


async def _get_available_tasks_for_role(
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
        # PMs get pending tasks AND tasks awaiting their review
        pending_params = {**params, "status": "pending"}
        pending_resp = await client.get("/tasks", params=pending_params)
        pending = pending_resp.json() if pending_resp.ok else []
        review_resp = await client.get(
            "/tasks", params={**params, "status": "awaiting_pm_review"}
        )
        review = review_resp.json() if review_resp.ok else []
        return pending + review

    # Developers get pending tasks only
    resp = await client.get("/tasks", params={**params, "status": "pending"})
    return resp.json() if resp.ok else []


async def _handle_task_scan(
    client: ApiClient, team: str | None, agent_id: str
) -> dict[str, Any]:
    """Handle task scanning."""
    paused_resp = await client.get("/tasks/my", params={"status": "paused"})
    paused_tasks = paused_resp.json() if paused_resp.ok else []

    # Get assigned tasks using /tasks/my
    # Includes: PM-assigned pending tasks + tasks being actively worked on
    assigned_resp = await client.get("/tasks/my")
    assigned_data = assigned_resp.json() if assigned_resp.ok else []
    # Include pending tasks (PM assigned) + active work statuses
    assigned_tasks = [
        t
        for t in assigned_data
        if t.get("status")
        in ["pending", "claimed", "in_progress", "verifying", "needs_revision"]
    ]

    # Get available tasks based on agent role
    agent_role = get_agent_role(agent_id)
    available_tasks = await _get_available_tasks_for_role(client, agent_role, team)

    # Filter out tasks already in assigned_tasks from available_tasks
    # (prevents PM-assigned pending tasks from appearing in both lists)
    assigned_ids = {t.get("id") for t in assigned_tasks}
    available_tasks = [t for t in available_tasks if t.get("id") not in assigned_ids]

    # Determine guidance based on role and available tasks
    if paused_tasks:
        guidance = (
            f"You have {len(paused_tasks)} paused task(s). "
            "Resume your paused work before claiming new tasks."
        )
    elif assigned_tasks:
        guidance = (
            f"You have {len(assigned_tasks)} active task(s). "
            "Continue working on your assigned tasks."
        )
    elif available_tasks:
        guidance = _get_available_tasks_guidance(available_tasks, agent_role)
    else:
        guidance = (
            "No tasks available. Call roboco_agent_idle() "
            "to signal availability, or check back later."
        )

    return {
        "paused_tasks": paused_tasks,
        "assigned_tasks": assigned_tasks,
        "available_tasks": available_tasks,
        "guidance": guidance,
    }


async def _handle_task_get(client: ApiClient, task_id: str) -> dict[str, Any]:
    """Handle getting task details."""
    resp = await client.get(f"/tasks/{task_id}")

    if resp.is_status(status.HTTP_404_NOT_FOUND):
        return _format_error_response(
            "NOT_FOUND",
            f"Task {task_id} not found",
        )

    task = resp.json()
    next_step, guidance = _get_next_step_guidance(task.get("status", ""))
    return _format_task_response(task, next_step, guidance)


def _check_blocking_tasks(active_tasks: list[dict]) -> dict[str, Any] | None:
    """Check for blocking active tasks. Returns error or None."""
    blocking_statuses = ["claimed", "in_progress", "verifying"]
    blocking = [t for t in active_tasks if t.get("status") in blocking_statuses]
    if blocking:
        return _format_error_response(
            "ALREADY_ACTIVE",
            f"You already have an active task: {blocking[0]['id']}. "
            "Complete or pause it before claiming a new task.",
            {"active_task_id": blocking[0]["id"]},
        )
    return None


def _check_paused_tasks(active_tasks: list[dict]) -> dict[str, Any] | None:
    """Check for paused tasks. Returns error or None."""
    paused = [t for t in active_tasks if t.get("status") == "paused"]
    if paused:
        return _format_error_response(
            "PAUSED_TASKS_EXIST",
            f"You have {len(paused)} paused task(s). "
            "Resume paused work before claiming new tasks.",
            {"paused_task_ids": [t["id"] for t in paused]},
        )
    return None


def _validate_task_claimable(task: dict, agent_role: str) -> dict[str, Any] | None:
    """Validate task can be claimed based on agent role. Returns error or None."""
    task_status = task.get("status")

    # Role-based claimable statuses
    claimable_statuses = {
        "qa": ["awaiting_qa"],
        "documenter": ["awaiting_documentation"],
    }

    # Default: developers and PMs can claim pending tasks
    allowed = claimable_statuses.get(agent_role, ["pending"])

    if task_status not in allowed:
        return _format_error_response(
            "INVALID_STATE",
            f"Cannot claim task in '{task_status}' status. "
            f"Your role ({agent_role}) can claim: {', '.join(allowed)}.",
            {"current_status": task_status, "allowed_statuses": allowed},
        )
    return None


async def _get_project_context(
    client: ApiClient, project_id: str
) -> dict[str, Any] | None:
    """Fetch project context if available."""
    resp = await client.get(f"/projects/{project_id}")
    if resp.ok:
        result: dict[str, Any] = resp.json()
        return result
    return None


async def _handle_task_claim(
    client: ApiClient, task_id: str, agent_id: str
) -> dict[str, Any]:
    """Handle task claiming."""
    active_resp = await client.get("/tasks/my")
    if active_resp.ok:
        active_tasks = active_resp.json()
        if error := _check_blocking_tasks(active_tasks):
            return error
        if error := _check_paused_tasks(active_tasks):
            return error

    task_resp = await client.get(f"/tasks/{task_id}")
    if task_resp.is_status(status.HTTP_404_NOT_FOUND):
        return _format_error_response("NOT_FOUND", f"Task {task_id} not found")

    task = task_resp.json()
    agent_role = get_agent_role(agent_id)
    if error := _validate_task_claimable(task, agent_role):
        return error

    claim_resp = await client.post(
        f"/tasks/{task_id}/claim",
        json={"agent_id": agent_id},
    )
    if not claim_resp.ok:
        return _format_error_response(
            "CLAIM_FAILED",
            "Failed to claim task",
            {"api_error": claim_resp.text},
        )

    claimed_task = claim_resp.json()

    project = None
    if claimed_task.get("project_id"):
        project = await _get_project_context(client, claimed_task["project_id"])

    return _format_task_response(
        claimed_task,
        "UNDERSTAND",
        "Task claimed successfully. "
        "Read the description and acceptance criteria carefully. "
        "Ask questions if ANYTHING is unclear - do not guess. "
        "When ready, create your plan with roboco_task_plan.",
        project=project,
    )


async def _validate_task_ownership(
    task: dict, agent_id: str, client: ApiClient
) -> dict[str, Any] | None:
    """Validate agent owns the task. Returns error or None."""
    assigned_to = task.get("assigned_to")
    if not assigned_to:
        return _format_error_response(
            "NOT_ASSIGNED",
            "This task is not assigned to anyone",
        )

    # Resolve agent_id (which may be a slug) to UUID for comparison
    agent_uuid = await resolve_agent_uuid(agent_id, client._get_headers())
    if not agent_uuid:
        return _format_error_response(
            "AGENT_NOT_FOUND",
            f"Could not resolve agent: {agent_id}",
        )

    if str(assigned_to) != agent_uuid:
        return _format_error_response(
            "NOT_OWNER",
            "You are not assigned to this task",
            {"assigned_to": assigned_to},
        )
    return None


def _validate_task_status_claimed(task: dict) -> dict[str, Any] | None:
    """Validate task is in claimed status. Returns error or None."""
    if task.get("status") != "claimed":
        return _format_error_response(
            "INVALID_STATE",
            f"Cannot submit plan for task in '{task.get('status')}' status. "
            "Task must be 'claimed'.",
            {"current_status": task.get("status")},
        )
    return None


def _build_plan_data(plan_params: dict[str, Any]) -> dict[str, Any]:
    """Build the plan data structure from params."""
    from uuid import uuid4

    return {
        "approach": plan_params["approach"],
        "sub_tasks": [
            {
                "id": st.get("id") or str(uuid4()),  # Use provided ID or generate one
                "title": st.get("title", ""),
                "description": st.get("description", ""),
                "order": i,
            }
            for i, st in enumerate(plan_params["sub_tasks"])
        ],
        "risks": [{"description": r} for r in (plan_params.get("risks") or [])],
        "open_questions": [
            {"question": q, "answered": False}
            for q in (plan_params.get("open_questions") or [])
        ],
    }


async def _handle_task_plan(
    client: ApiClient,
    task_id: str,
    plan_params: dict[str, Any],
    agent_id: str,
) -> dict[str, Any]:
    """Handle task planning."""
    task_resp = await client.get(f"/tasks/{task_id}")
    if task_resp.is_status(status.HTTP_404_NOT_FOUND):
        return _format_error_response("NOT_FOUND", f"Task {task_id} not found")

    task = task_resp.json()
    if error := await _validate_task_ownership(task, agent_id, client):
        return error
    if error := _validate_task_status_claimed(task):
        return error

    plan_data = _build_plan_data(plan_params)
    update_resp = await client.patch(f"/tasks/{task_id}", json={"plan": plan_data})
    if not update_resp.ok:
        return _format_error_response(
            "UPDATE_FAILED",
            "Failed to save plan",
            {"api_error": update_resp.text},
        )

    updated_task = update_resp.json()

    open_questions = plan_params.get("open_questions")
    if open_questions:
        return _format_task_response(
            updated_task,
            "ASK_QUESTIONS",
            f"Plan saved but you have {len(open_questions)} open question(s). "
            "Ask these questions in your cell channel before starting. "
            "Do NOT proceed until questions are answered.",
        )

    return _format_task_response(
        updated_task,
        "START",
        "Plan saved. Call roboco_task_start to begin implementation.",
    )


async def _validate_task_start(
    task: dict[str, Any], agent_id: str, client: ApiClient
) -> dict[str, Any] | None:
    """Validate task can be started. Returns error dict or None."""
    if error := await _validate_task_ownership(task, agent_id, client):
        return error

    task_status = task.get("status")
    # Valid statuses to start/resume work:
    # - claimed: Developer just claimed a pending task
    # - paused: Developer resuming paused work
    # - needs_revision: Developer resuming after QA rejection
    valid_start_statuses = ["claimed", "paused", "needs_revision"]
    if task_status not in valid_start_statuses:
        return _format_error_response(
            "INVALID_STATE",
            f"Cannot start task in '{task_status}' status. "
            "Task must be 'claimed', 'paused', or 'needs_revision'.",
            {"current_status": task_status},
        )

    # Only require plan for newly claimed tasks, not for resuming revision
    if task_status == "claimed" and not task.get("plan"):
        return _format_error_response(
            "NO_PLAN",
            "Cannot start without a plan. Call roboco_task_plan first.",
        )

    # Only check open questions for newly claimed tasks
    if task_status == "claimed":
        plan = task.get("plan", {})
        unanswered = [
            q for q in plan.get("open_questions", []) if not q.get("answered")
        ]
        if unanswered:
            return _format_error_response(
                "UNANSWERED_QUESTIONS",
                f"Cannot start with {len(unanswered)} "
                "unanswered question(s). "
                "Get answers first, then update the plan.",
                {"questions": [q.get("question") for q in unanswered]},
            )

    return None


async def _handle_task_start(
    client: ApiClient, task_id: str, agent_id: str
) -> dict[str, Any]:
    """Handle task start."""
    task_resp = await client.get(f"/tasks/{task_id}")
    if task_resp.is_status(status.HTTP_404_NOT_FOUND):
        return _format_error_response("NOT_FOUND", f"Task {task_id} not found")

    task = task_resp.json()

    if validation_error := await _validate_task_start(task, agent_id, client):
        return validation_error

    # Start the task
    start_resp = await client.post(f"/tasks/{task_id}/start")

    if not start_resp.ok:
        return _format_error_response(
            "START_FAILED",
            "Failed to start task",
            {"api_error": start_resp.text},
        )

    return _format_task_response(
        start_resp.json(),
        "EXECUTE",
        "Task started. Work through your plan step by step:\n"
        "1. Implement each sub-task\n"
        "2. Commit frequently with clear messages\n"
        "3. Call roboco_task_progress to update status\n"
        "4. If blocked, call roboco_task_block immediately\n"
        "5. When done, call roboco_task_submit_verification",
    )


async def _handle_task_progress(
    client: ApiClient,
    task_id: str,
    message: str,
    percentage: int,
    agent_id: str,
) -> dict[str, Any]:
    """Handle task progress update."""
    # Validate percentage is in valid range
    if not _MIN_PERCENTAGE <= percentage <= _MAX_PERCENTAGE:
        return _format_error_response(
            "INVALID_PERCENTAGE",
            f"Percentage must be between {_MIN_PERCENTAGE} and {_MAX_PERCENTAGE}",
        )

    task_resp = await client.get(f"/tasks/{task_id}")
    if task_resp.is_status(status.HTTP_404_NOT_FOUND):
        return _format_error_response("NOT_FOUND", f"Task {task_id} not found")

    task = task_resp.json()

    if error := await _validate_task_ownership(task, agent_id, client):
        return error

    # Allow progress updates for active work statuses
    active_statuses = {
        "in_progress",
        "verifying",
        "awaiting_qa",
        "awaiting_documentation",
    }
    if task.get("status") not in active_statuses:
        return _format_error_response(
            "INVALID_STATE",
            f"Can only update progress for active tasks. Current: {task.get('status')}",
        )

    # Add progress update
    progress_resp = await client.post(
        f"/tasks/{task_id}/progress",
        json={
            "agent_id": agent_id,
            "message": message,
            "percentage": percentage,
        },
    )

    if not progress_resp.ok:
        return _format_error_response(
            "UPDATE_FAILED",
            "Failed to update progress",
        )

    updated_task = progress_resp.json()

    return _format_task_response(
        updated_task,
        "CONTINUE",
        "Progress recorded. Keep working through your plan.",
    )


async def _handle_task_block(
    client: ApiClient,
    data: TaskBlockInput,
    agent_id: str,
) -> dict[str, Any]:
    """Handle task blocking via the soft-block endpoint."""
    task_resp = await client.get(f"/tasks/{data.task_id}")
    if task_resp.is_status(status.HTTP_404_NOT_FOUND):
        return _format_error_response("NOT_FOUND", f"Task {data.task_id} not found")

    task = task_resp.json()

    if error := await _validate_task_ownership(task, agent_id, client):
        return error

    if task.get("status") != "in_progress":
        return _format_error_response(
            "INVALID_STATE",
            "Can only block in_progress tasks",
        )

    # Use the soft-block endpoint which handles status change and notes
    block_resp = await client.post(
        f"/tasks/{data.task_id}/soft-block",
        json={
            "reason": data.reason,
            "blocker_type": data.blocker_type,
            "what_needed": data.what_needed,
        },
    )

    if not block_resp.ok:
        return _format_error_response(
            "BLOCK_FAILED",
            "Failed to block task",
            {"status_code": block_resp.status_code, "detail": block_resp.text},
        )

    blocked_task = block_resp.json()

    return _format_task_response(
        blocked_task,
        "WAIT_OR_SWITCH",
        f"Task blocked: {data.reason}\n\n"
        "Options:\n"
        "1. WAIT - If resolution expected soon, poll for updates\n"
        "2. SWITCH - Call roboco_task_scan to work on another task\n"
        "3. ESCALATE - Message your PM if this is urgent\n\n"
        "The blocker has been communicated. "
        "You'll be notified when resolved.",
    )


async def _handle_task_unblock(
    client: ApiClient, task_id: str, agent_id: str
) -> dict[str, Any]:
    """Handle task unblocking."""
    task_resp = await client.get(f"/tasks/{task_id}")
    if task_resp.is_status(status.HTTP_404_NOT_FOUND):
        return _format_error_response("NOT_FOUND", f"Task {task_id} not found")

    task = task_resp.json()

    if error := await _validate_task_ownership(task, agent_id, client):
        return error

    if task.get("status") != "blocked":
        return _format_error_response(
            "INVALID_STATE",
            "Task is not blocked",
        )

    unblock_resp = await client.post(f"/tasks/{task_id}/unblock")

    if not unblock_resp.ok:
        return _format_error_response("UNBLOCK_FAILED", "Failed to unblock task")

    unblocked_task = unblock_resp.json()

    return _format_task_response(
        unblocked_task,
        "CONTINUE",
        "Task unblocked. Resume from your last checkpoint.",
    )


async def _handle_task_pause(
    client: ApiClient,
    data: TaskPauseInput,
    agent_id: str,
) -> dict[str, Any]:
    """Handle task pausing."""
    task_resp = await client.get(f"/tasks/{data.task_id}")
    if task_resp.is_status(status.HTTP_404_NOT_FOUND):
        return _format_error_response("NOT_FOUND", f"Task {data.task_id} not found")

    task = task_resp.json()

    if error := await _validate_task_ownership(task, agent_id, client):
        return error

    if task.get("status") != "in_progress":
        return _format_error_response(
            "INVALID_STATE",
            "Can only pause in_progress tasks",
        )

    # Add checkpoint
    await client.post(
        f"/tasks/{data.task_id}/checkpoint",
        json={
            "agent_id": agent_id,
            "state_summary": data.checkpoint_summary,
            "remaining_work": data.remaining_work,
            "notes": data.reason,
        },
    )

    # Pause the task
    pause_resp = await client.post(f"/tasks/{data.task_id}/pause")

    if not pause_resp.ok:
        return _format_error_response("PAUSE_FAILED", "Failed to pause task")

    paused_task = pause_resp.json()

    return _format_task_response(
        paused_task,
        "SCAN_FOR_WORK",
        f"Task paused. Checkpoint saved.\n"
        f"Reason: {data.reason}\n\n"
        "To resume later, call roboco_task_start with this task_id.\n"
        "Now call roboco_task_scan to find your next task.",
    )


async def _handle_task_submit_verification(
    client: ApiClient, task_id: str, agent_id: str
) -> dict[str, Any]:
    """Handle task verification submission."""
    task_resp = await client.get(f"/tasks/{task_id}")
    if task_resp.is_status(status.HTTP_404_NOT_FOUND):
        return _format_error_response("NOT_FOUND", f"Task {task_id} not found")

    task = task_resp.json()

    if error := await _validate_task_ownership(task, agent_id, client):
        return error

    if task.get("status") != "in_progress":
        return _format_error_response(
            "INVALID_STATE",
            "Can only submit in_progress tasks for verification",
        )

    # Check for evidence of work done (commits OR progress updates)
    # Non-code tasks (testing, research, docs) may not have commits
    has_commits = bool(task.get("commits"))
    has_progress = bool(task.get("progress_updates"))
    has_checkpoints = bool(task.get("checkpoints"))

    if not (has_commits or has_progress or has_checkpoints):
        return _format_error_response(
            "NO_WORK_EVIDENCE",
            "No evidence of work found. Add commits with roboco_task_add_commit "
            "or update progress with roboco_task_progress before verification.",
        )

    verify_resp = await client.post(f"/tasks/{task_id}/verify")

    if not verify_resp.ok:
        return _format_error_response(
            "VERIFY_FAILED", "Failed to submit for verification"
        )

    verifying_task = verify_resp.json()

    # Build verification checklist from acceptance criteria
    criteria = task.get("acceptance_criteria", [])
    checklist = "\n".join(f"- [ ] {c}" for c in criteria)

    return _format_task_response(
        verifying_task,
        "VERIFY",
        f"Self-verify against acceptance criteria:\n\n{checklist}\n\n"
        "Check EACH criterion. Run tests. Check edge cases.\n"
        "When ALL pass, call roboco_task_submit_qa.\n"
        "If issues found, fix them and update progress.",
    )


async def _handle_task_submit_qa(
    client: ApiClient,
    task_id: str,
    dev_notes: str,
    handoff_summary: str,
    agent_id: str,
) -> dict[str, Any]:
    """Handle task QA submission."""
    # Validate inputs
    if not dev_notes or not handoff_summary:
        return _format_error_response(
            "MISSING_NOTES",
            "Both dev_notes and handoff_summary are required for QA submission.",
        )

    # Validate task exists and ownership
    task_resp = await client.get(f"/tasks/{task_id}")
    if task_resp.is_status(status.HTTP_404_NOT_FOUND):
        return _format_error_response("NOT_FOUND", f"Task {task_id} not found")

    task = task_resp.json()
    if error := await _validate_task_ownership(task, agent_id, client):
        return error

    # Validate state
    if task.get("status") != "verifying":
        return _format_error_response(
            "INVALID_STATE", "Can only submit verified tasks for QA"
        )

    # Save dev notes and handoff summary, then submit for QA
    combined_notes = f"{dev_notes}\n\n---\nHandoff Summary:\n{handoff_summary}"
    notes_resp = await client.patch(
        f"/tasks/{task_id}", json={"dev_notes": combined_notes}
    )
    if not notes_resp.ok:
        return _format_error_response(
            "NOTES_SAVE_FAILED",
            "Failed to save dev notes. QA submission aborted.",
        )

    qa_resp = await client.post(f"/tasks/{task_id}/submit-qa")
    return (
        _format_task_response(
            qa_resp.json(),
            "WAIT_FOR_QA",
            "Task submitted for QA review.\n"
            "You will be notified of the result.\n"
            "In the meantime, call roboco_task_scan for other work.",
        )
        if qa_resp.ok
        else _format_error_response("SUBMIT_FAILED", "Failed to submit for QA")
    )


async def _handle_task_qa_pass(
    client: ApiClient,
    task_id: str,
    qa_notes: str,
    agent_id: str,
) -> dict[str, Any]:
    """Handle task QA pass."""
    # Check if agent has QA role using canonical role lookup
    agent_role = get_agent_role(agent_id)
    if agent_role != "qa":
        return _format_error_response(
            "NOT_QA",
            "Only QA agents can pass tasks through QA review.",
            {"your_role": agent_role},
        )

    task_resp = await client.get(f"/tasks/{task_id}")
    if task_resp.is_status(status.HTTP_404_NOT_FOUND):
        return _format_error_response("NOT_FOUND", f"Task {task_id} not found")

    task = task_resp.json()

    if task.get("status") != "awaiting_qa":
        return _format_error_response(
            "INVALID_STATE",
            "Task is not awaiting QA",
        )

    # Check QA is not reviewing own work
    # Check against original developer stored in quick_context
    quick_context = task.get("quick_context")
    original_dev = extract_original_developer(quick_context)

    # Resolve agent_id to UUID for proper comparison
    agent_uuid = await resolve_agent_uuid(agent_id, client._get_headers())
    if original_dev and agent_uuid and agent_uuid == original_dev:
        return _format_error_response(
            "SELF_REVIEW",
            "Cannot review your own work.",
        )

    pass_resp = await client.post(
        f"/tasks/{task_id}/pass-qa",
        json={"notes": qa_notes},
    )

    if not pass_resp.ok:
        return _format_error_response(
            "QA_FAILED",
            "Failed to pass QA",
            {"status_code": pass_resp.status_code, "api_error": pass_resp.text},
        )

    passed_task = pass_resp.json()

    return _format_task_response(
        passed_task,
        "NOTIFY_DEV",
        "Task passed QA. Documenter will be notified.\n"
        "Call roboco_task_scan for next QA task.",
    )


async def _handle_task_qa_fail(
    client: ApiClient,
    task_id: str,
    qa_notes: str,
    issues: list[str],
    agent_id: str,
) -> dict[str, Any]:
    """Handle task QA failure."""
    # Check if agent has QA role using canonical role lookup
    agent_role = get_agent_role(agent_id)
    if agent_role != "qa":
        return _format_error_response(
            "NOT_QA",
            "Only QA agents can fail tasks in QA review.",
            {"your_role": agent_role},
        )

    if not issues:
        return _format_error_response(
            "NO_ISSUES",
            "Must specify at least one issue when failing QA.",
        )

    task_resp = await client.get(f"/tasks/{task_id}")
    if task_resp.is_status(status.HTTP_404_NOT_FOUND):
        return _format_error_response("NOT_FOUND", f"Task {task_id} not found")

    task = task_resp.json()

    if task.get("status") != "awaiting_qa":
        return _format_error_response(
            "INVALID_STATE",
            "Task is not awaiting QA",
        )

    full_notes = f"{qa_notes}\n\nIssues:\n" + "\n".join(f"- {i}" for i in issues)

    fail_resp = await client.post(
        f"/tasks/{task_id}/fail-qa",
        json={"notes": full_notes},
    )

    if not fail_resp.ok:
        return _format_error_response(
            "QA_FAILED",
            "Failed to fail QA",
            {"status_code": fail_resp.status_code, "api_error": fail_resp.text},
        )

    failed_task = fail_resp.json()

    return _format_task_response(
        failed_task,
        "NOTIFY_DEV",
        f"Task returned for revision with {len(issues)} issue(s).\n"
        "Developer will be notified.\n"
        "Call roboco_task_scan for next QA task.",
    )


async def _handle_docs_complete(
    client: ApiClient,
    task_id: str,
    agent_id: str,
    doc_notes: str | None = None,
) -> dict[str, Any]:
    """Handle documentation completion (documenter only)."""
    # Check if agent is a documenter
    agent_role = get_agent_role(agent_id)
    if agent_role != "documenter":
        return _format_error_response(
            "NOT_DOCUMENTER",
            "Only documenters can mark documentation as complete.",
            {"your_role": agent_role},
        )

    task_resp = await client.get(f"/tasks/{task_id}")
    if task_resp.is_status(status.HTTP_404_NOT_FOUND):
        return _format_error_response("NOT_FOUND", f"Task {task_id} not found")

    task = task_resp.json()

    if task.get("status") != "awaiting_documentation":
        return _format_error_response(
            "INVALID_STATE",
            "Task must be awaiting documentation to mark docs complete",
        )

    payload = {"notes": doc_notes} if doc_notes else {}
    docs_resp = await client.post(f"/tasks/{task_id}/docs-complete", json=payload)

    if not docs_resp.ok:
        return _format_error_response(
            "DOCS_COMPLETE_FAILED",
            "Failed to mark documentation complete",
            {"status_code": docs_resp.status_code, "api_error": docs_resp.text},
        )

    updated_task = docs_resp.json()

    return _format_task_response(
        updated_task,
        "AWAITING_PM",
        "Documentation complete! Task is now awaiting PM review.\n"
        "The Cell PM will review and complete the task.\n"
        "Call roboco_task_scan for next documentation task.",
    )


async def _handle_task_complete(
    client: ApiClient,
    task_id: str,
    agent_id: str,
) -> dict[str, Any]:
    """Handle task completion (PM only)."""
    # Check if agent can complete tasks (PM role)
    if not can_cancel_tasks(agent_id):  # Same roles that can cancel can complete
        role = get_agent_role(agent_id)
        return _format_error_response(
            "NOT_PM",
            "Only PMs can complete tasks after reviewing.",
            {"your_role": role},
        )

    task_resp = await client.get(f"/tasks/{task_id}")
    if task_resp.is_status(status.HTTP_404_NOT_FOUND):
        return _format_error_response("NOT_FOUND", f"Task {task_id} not found")

    task = task_resp.json()

    if task.get("status") != "awaiting_pm_review":
        return _format_error_response(
            "INVALID_STATE",
            "Task must be awaiting PM review to complete. "
            "Documenter should call roboco_task_docs_complete first.",
        )

    complete_resp = await client.post(f"/tasks/{task_id}/complete")

    if not complete_resp.ok:
        return _format_error_response(
            "COMPLETE_FAILED",
            "Failed to complete task",
            {"status_code": complete_resp.status_code, "api_error": complete_resp.text},
        )

    completed_task = complete_resp.json()

    return _format_task_response(
        completed_task,
        "DONE",
        "Task completed successfully!\nCall roboco_task_scan for more work.",
    )


async def _handle_task_cancel(
    client: ApiClient,
    task_id: str,
    agent_id: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Handle task cancellation (PM and board only)."""
    # Check permission first
    if not can_cancel_tasks(agent_id):
        role = get_agent_role(agent_id)
        return _format_error_response(
            "NOT_AUTHORIZED",
            "Only PMs and board members can cancel tasks",
            {"your_role": role},
        )

    # Get task to verify it exists
    task_resp = await client.get(f"/tasks/{task_id}")
    if not task_resp.ok:
        return _format_error_response("NOT_FOUND", f"Task {task_id} not found")

    task = task_resp.json()
    current_status = task.get("status")

    # Terminal states can't be cancelled
    if current_status in ("completed", "cancelled"):
        return _format_error_response(
            "INVALID_STATE",
            f"Cannot cancel task in '{current_status}' status",
        )

    # Cancel the task
    cancel_resp = await client.post(f"/tasks/{task_id}/cancel")

    if not cancel_resp.ok:
        return _format_error_response(
            "CANCEL_FAILED",
            "Failed to cancel task",
            {
                "status_code": cancel_resp.status_code,
                "api_error": cancel_resp.text,
            },
        )

    cancelled_task = cancel_resp.json()

    return _format_task_response(
        cancelled_task,
        "CANCELLED",
        f"Task cancelled.{' Reason: ' + reason if reason else ''}",
    )


async def _handle_agent_idle(client: ApiClient, agent_id: str) -> dict[str, Any]:
    """Handle agent going idle (no work available)."""
    # First, check if agent has any in-progress tasks
    # Use /tasks/my endpoint which properly uses authenticated agent context
    try:
        scan_resp = await client.get("/tasks/my", params={"status": "in_progress"})
        if scan_resp.ok:
            tasks = scan_resp.json()  # /tasks/my returns list directly
            if tasks:
                # Agent has in-progress tasks - they must handle them first
                task_info = [
                    {"id": t.get("id"), "title": t.get("title")} for t in tasks
                ]
                return _format_error_response(
                    "TASKS_IN_PROGRESS",
                    (
                        "You have in-progress tasks. Handle them before going "
                        "idle using: roboco_task_pause (to pause), "
                        "roboco_task_submit_qa (if done), or "
                        "roboco_task_complete (if approved)."
                    ),
                    {"tasks": task_info},
                )
    except Exception:
        # If check fails, continue to mark idle (fail open)
        pass

    try:
        # Signal to orchestrator that this agent is idle
        resp = await client.post(
            f"/orchestrator/agents/{agent_id}/mark-waiting",
            params={"waiting_for": "task_assignment"},
        )
    except Exception as e:
        return _format_error_response(
            "CONNECTION_ERROR",
            f"Failed to connect to orchestrator: {type(e).__name__}",
        )

    if resp.is_status(status.HTTP_204_NO_CONTENT):
        return {
            "status": "idle",
            "message": (
                "You are now in WAITING state. Your container will terminate "
                "to save resources. You will be respawned when work is available."
            ),
            "action": "EXIT_GRACEFULLY",
        }

    # Handle specific error codes
    if resp.is_status(status.HTTP_503_SERVICE_UNAVAILABLE):
        return _format_error_response(
            "ORCHESTRATOR_UNAVAILABLE",
            "Orchestrator is not running. Cannot mark idle state.",
            {"detail": resp.text},
        )

    return _format_error_response(
        "IDLE_FAILED",
        "Failed to signal idle state to orchestrator",
        {"status_code": resp.status_code, "detail": resp.text},
    )


# =============================================================================
# PM DELEGATION HANDLERS
# =============================================================================


async def _handle_task_create(
    client: ApiClient,
    input_data: TaskCreateInput,
    agent_id: str,
) -> dict[str, Any]:
    """Handle task creation by PM."""
    agent_team = get_agent_team(agent_id)

    # Validate PM role
    if not can_create_tasks(agent_id):
        return _format_error_response(
            "PERMISSION_DENIED",
            "Only PMs and management can create tasks",
            {"role": get_agent_role(agent_id)},
        )

    # Cell PM can only create tasks for their team
    role = get_agent_role(agent_id)
    if role == "cell_pm" and input_data.team != agent_team:
        return _format_error_response(
            "TEAM_MISMATCH",
            f"Cell PM can only create tasks for their team ({agent_team})",
            {"requested_team": input_data.team, "agent_team": agent_team},
        )

    # Build task payload
    payload: dict[str, Any] = {
        "title": input_data.title,
        "description": input_data.description,
        "acceptance_criteria": input_data.acceptance_criteria,
        "team": input_data.team,
        "priority": input_data.priority,
        "estimated_complexity": input_data.complexity,
    }
    if input_data.parent_task_id:
        payload["parent_task_id"] = input_data.parent_task_id

    # Create the task
    try:
        create_resp = await client.post("/tasks", json=payload)
    except Exception as e:
        return _format_error_response(
            "CONNECTION_ERROR",
            f"Failed to connect to API: {type(e).__name__}",
        )

    if not create_resp.is_status(status.HTTP_201_CREATED):
        return _format_error_response(
            "CREATE_FAILED",
            "Failed to create task",
            {"status_code": create_resp.status_code, "detail": create_resp.text},
        )

    task = create_resp.json()

    # If assigned_to specified, set assignee but keep pending (don't claim)
    # Orchestrator will spawn the agent who will then claim it
    if input_data.assigned_to:
        assigned_task, _ = await _assign_task_to_agent(
            client, task["id"], input_data.assigned_to
        )
        if assigned_task:
            task = assigned_task

    guidance = f"Task created successfully. ID: {task['id']}. "
    if input_data.assigned_to:
        guidance += (
            f"Assigned to: {input_data.assigned_to} (pending). "
            "Orchestrator will spawn them to claim and work on it."
        )
    else:
        guidance += "Task is pending - assign it or let orchestrator route it."

    return _format_task_response(task, "CREATED", guidance)


def _validate_cell_pm_assignment(
    role: str,
    agent_team: str | None,
    task: dict[str, Any],
    assignee: str,
) -> dict[str, Any] | None:
    """Validate Cell PM assignment restrictions. Returns error dict or None if valid."""
    if role != "cell_pm":
        return None

    task_team = task.get("team")
    if task_team != agent_team:
        return _format_error_response(
            "TEAM_MISMATCH",
            f"Cell PM can only assign tasks in their team ({agent_team})",
            {"task_team": task_team},
        )

    assignee_team = get_agent_team(assignee)
    if assignee_team and assignee_team != agent_team:
        return _format_error_response(
            "ASSIGNEE_MISMATCH",
            "Cannot assign to agent outside your team",
            {"assignee_team": assignee_team, "your_team": agent_team},
        )

    return None


async def _fetch_task_for_assignment(
    client: ApiClient,
    task_id: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Fetch task for assignment. Returns (task, error) tuple."""
    try:
        task_resp = await client.get(f"/tasks/{task_id}")
    except Exception as e:
        return None, _format_error_response(
            "CONNECTION_ERROR",
            f"Failed to connect to API: {type(e).__name__}",
        )

    if task_resp.is_status(status.HTTP_404_NOT_FOUND):
        return None, _format_error_response("NOT_FOUND", f"Task {task_id} not found")

    if not task_resp.ok:
        return None, _format_error_response(
            "FETCH_FAILED",
            "Failed to fetch task",
            {"status_code": task_resp.status_code},
        )

    return task_resp.json(), None


async def _assign_task_to_agent(
    client: ApiClient,
    task_id: str,
    assignee: str,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """
    Assign task to agent by setting assigned_to and resetting to pending.

    This is different from claiming - assignment means "this agent should work
    on this task". The orchestrator will then spawn the agent who will claim it.

    Returns (assigned_task, error) tuple.
    """
    # Resolve assignee slug to UUID
    assignee_id = await resolve_agent_uuid(assignee, client._get_headers())
    if not assignee_id:
        return None, _format_error_response(
            "INVALID_ASSIGNEE",
            f"Could not resolve agent: {assignee}",
            {"assignee": assignee},
        )

    try:
        # PATCH to set assigned_to and reset status to pending
        assign_resp = await client.patch(
            f"/tasks/{task_id}",
            json={"assigned_to": assignee_id, "status": "pending"},
        )
    except Exception as e:
        return None, _format_error_response(
            "CONNECTION_ERROR",
            f"Failed to connect to API: {type(e).__name__}",
        )

    if not assign_resp.ok:
        return None, _format_error_response(
            "ASSIGN_FAILED",
            "Failed to assign task",
            {"status_code": assign_resp.status_code, "detail": assign_resp.text},
        )

    return assign_resp.json(), None


async def _handle_task_assign(
    client: ApiClient,
    input_data: TaskAssignInput,
    agent_id: str,
) -> dict[str, Any]:
    """Handle task assignment by PM."""
    agent_team = get_agent_team(agent_id)
    role = get_agent_role(agent_id)

    # Validate PM role
    if not can_assign_tasks(agent_id):
        return _format_error_response(
            "PERMISSION_DENIED",
            "Only PMs and management can assign tasks",
            {"role": role},
        )

    # Get task details first
    task, error = await _fetch_task_for_assignment(client, input_data.task_id)
    if error or task is None:
        return error or _format_error_response("FETCH_FAILED", "No task returned")

    # Validate Cell PM restrictions
    validation_error = _validate_cell_pm_assignment(
        role, agent_team, task, input_data.assignee
    )
    if validation_error:
        return validation_error

    # Assign task to agent (sets assigned_to and resets to pending)
    # This is NOT claiming - the dev will claim when spawned
    assigned_task, assign_error = await _assign_task_to_agent(
        client, input_data.task_id, input_data.assignee
    )
    if assign_error or assigned_task is None:
        return assign_error or _format_error_response("ASSIGN_FAILED", "No task")

    guidance = (
        f"Task assigned to {input_data.assignee} and set to pending. "
        "Orchestrator will spawn them to claim and work on it."
    )
    return _format_task_response(assigned_task, "ASSIGNED", guidance)


async def _handle_task_escalate(
    client: ApiClient,
    input_data: TaskEscalateInput,
    agent_id: str,
) -> dict[str, Any]:
    """Handle task escalation up the hierarchy."""
    # Determine and resolve escalation target upfront
    target = input_data.escalate_to or get_escalation_target(agent_id)
    if not target:
        return _format_error_response(
            "NO_ESCALATION_PATH",
            f"No escalation path from agent: {agent_id}",
            {"role": get_agent_role(agent_id)},
        )

    target_uuid = await resolve_agent_uuid(target, client._get_headers())
    if not target_uuid:
        return _format_error_response(
            "INVALID_TARGET",
            f"Could not resolve escalation target: {target}",
        )

    try:
        # Get task details
        task_resp = await client.get(f"/tasks/{input_data.task_id}")

        if task_resp.is_status(status.HTTP_404_NOT_FOUND):
            return _format_error_response(
                "NOT_FOUND", f"Task {input_data.task_id} not found"
            )

        task = task_resp.json()

        # Create escalation notification
        notif_resp = await client.post(
            "/notifications",
            json={
                "type": "blocker_escalation",
                "to_agents": [target_uuid],
                "subject": f"Escalation: {task.get('title', 'Unknown task')}",
                "body": (
                    f"Task {input_data.task_id} escalated by {agent_id}.\n\n"
                    f"Reason: {input_data.reason}"
                ),
                "related_task_id": input_data.task_id,
                "priority": "high",
            },
        )

        if not notif_resp.ok and not notif_resp.is_status(status.HTTP_201_CREATED):
            return _format_error_response(
                "ESCALATION_FAILED",
                "Failed to send escalation notification",
                {"status_code": notif_resp.status_code, "detail": notif_resp.text},
            )
    except Exception as e:
        return _format_error_response(
            "CONNECTION_ERROR",
            f"Failed to connect to API: {type(e).__name__}",
        )

    guidance = (
        f"Task escalated to {target}. Reason: {input_data.reason}. "
        "They will be notified and can reassign or provide guidance."
    )
    return _format_task_response(task, "ESCALATED", guidance)


# =============================================================================
# MCP SERVER FACTORY
# =============================================================================


def create_task_mcp_server(agent_id: str) -> FastMCP:
    """
    Create a Task MCP server for a specific agent.

    The agent_id is embedded in the server to enforce ownership rules.

    Args:
        agent_id: The agent identifier (e.g., "be-dev-1")

    Returns:
        Configured FastMCP server
    """
    mcp = FastMCP(f"roboco-task-{agent_id}", json_response=True)

    # Create shared API client for this agent
    client = ApiClient(agent_id)

    @mcp.tool()
    async def roboco_task_scan(
        team: str | None = None,
    ) -> dict[str, Any]:
        """
        Scan for available tasks.

        Returns tasks in priority order:
        1. PAUSED tasks (yours) - must resume these first
        2. ASSIGNED tasks (explicitly given to you)
        3. AVAILABLE tasks (team pool, can claim)

        Args:
            team: Optional team filter (backend, frontend, uxui)

        Returns:
            Dict with paused/assigned/available tasks and guidance
        """
        return await _handle_task_scan(client, team, agent_id)

    @mcp.tool()
    async def roboco_task_get(task_id: str) -> dict[str, Any]:
        """
        Get detailed information about a task.

        Args:
            task_id: The task UUID

        Returns:
            Task details with current status and guidance
        """
        return await _handle_task_get(client, task_id)

    @mcp.tool()
    async def roboco_task_claim(task_id: str) -> dict[str, Any]:
        """
        Claim a task to work on it.

        ENFORCEMENT:
        - Task must be in 'pending' status
        - You cannot claim if you have an active (non-waiting) task
        - Paused tasks must be resumed first

        Args:
            task_id: The task UUID to claim

        Returns:
            Claimed task with project context and next step guidance
        """
        return await _handle_task_claim(client, task_id, agent_id)

    @mcp.tool()
    async def roboco_task_plan(
        task_id: str,
        approach: str,
        steps: list[dict[str, str]],
        risks: list[str] | None = None,
        open_questions: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Submit implementation plan for a task.

        NOTE: The 'steps' parameter creates a CHECKLIST within this task's plan.
        These are NOT real database subtasks. To create actual subtasks that
        other agents can claim and work on, use roboco_task_create() with
        parent_task_id instead.

        ENFORCEMENT:
        - Task must be in 'claimed' status
        - You must be the assigned agent

        Args:
            task_id: The task UUID
            approach: High-level approach description
            steps: List of plan steps (checklist) with 'title' and 'description'
            risks: Optional list of identified risks
            open_questions: Optional questions (BLOCKS start if present)

        Returns:
            Updated task with guidance
        """
        plan_params = {
            "approach": approach,
            "sub_tasks": steps,  # Internal storage still uses sub_tasks
            "risks": risks,
            "open_questions": open_questions,
        }
        return await _handle_task_plan(client, task_id, plan_params, agent_id)

    @mcp.tool()
    async def roboco_task_start(task_id: str) -> dict[str, Any]:
        """
        Start working on a task.

        ENFORCEMENT:
        - Task must be in 'claimed' or 'paused' status
        - Plan must be submitted first (for claimed tasks)
        - You must be the assigned agent

        Args:
            task_id: The task UUID

        Returns:
            Updated task with execution guidance
        """
        return await _handle_task_start(client, task_id, agent_id)

    @mcp.tool()
    async def roboco_task_progress(
        task_id: str,
        message: str,
        percentage: int,
    ) -> dict[str, Any]:
        """
        Update task progress.

        ENFORCEMENT:
        - Percentage is REQUIRED (0-100) to show real progress
        - Message must describe what was accomplished

        Args:
            task_id: The task UUID
            message: Progress update message describing work done
            percentage: Completion percentage (0-100), required

        Returns:
            Updated task
        """
        return await _handle_task_progress(
            client, task_id, message, percentage, agent_id
        )

    @mcp.tool()
    async def roboco_task_block(
        task_id: str,
        reason: str,
        blocker_type: str,
        what_needed: str,
    ) -> dict[str, Any]:
        """
        Mark task as blocked.

        ENFORCEMENT:
        - Task must be in 'in_progress' status
        - Reason and what_needed are required

        Args:
            task_id: The task UUID
            reason: Why the task is blocked
            blocker_type: Type (external/internal/question/dependency)
            what_needed: What is needed to unblock

        Returns:
            Updated task with options
        """
        data = TaskBlockInput(
            task_id=task_id,
            reason=reason,
            blocker_type=blocker_type,
            what_needed=what_needed,
        )
        return await _handle_task_block(client, data, agent_id)

    @mcp.tool()
    async def roboco_task_unblock(task_id: str) -> dict[str, Any]:
        """
        Unblock a task and resume work.

        ENFORCEMENT:
        - Task must be in 'blocked' status
        - You must be the assigned agent

        Args:
            task_id: The task UUID

        Returns:
            Updated task ready for work
        """
        return await _handle_task_unblock(client, task_id, agent_id)

    @mcp.tool()
    async def roboco_task_pause(
        task_id: str,
        reason: str,
        checkpoint_summary: str,
        remaining_work: list[str],
    ) -> dict[str, Any]:
        """
        Pause a task (e.g., for higher priority work).

        ENFORCEMENT:
        - Task must be in 'in_progress' status
        - Checkpoint is required for context restoration

        Args:
            task_id: The task UUID
            reason: Why pausing
            checkpoint_summary: Summary of current state
            remaining_work: List of remaining sub-tasks

        Returns:
            Paused task with resume instructions
        """
        data = TaskPauseInput(
            task_id=task_id,
            reason=reason,
            checkpoint_summary=checkpoint_summary,
            remaining_work=remaining_work,
        )
        return await _handle_task_pause(client, data, agent_id)

    @mcp.tool()
    async def roboco_task_submit_verification(
        task_id: str,
    ) -> dict[str, Any]:
        """
        Submit task for self-verification.

        ENFORCEMENT:
        - Task must be in 'in_progress' status
        - At least one commit should exist

        Args:
            task_id: The task UUID

        Returns:
            Task in verifying status with checklist
        """
        return await _handle_task_submit_verification(client, task_id, agent_id)

    @mcp.tool()
    async def roboco_task_submit_qa(
        task_id: str,
        dev_notes: str,
        handoff_summary: str,
    ) -> dict[str, Any]:
        """
        Submit task for QA review.

        ENFORCEMENT:
        - Task must be in 'verifying' status
        - Dev notes and handoff summary required

        Args:
            task_id: The task UUID
            dev_notes: Journey notes from development
            handoff_summary: Summary for QA reviewer

        Returns:
            Task submitted for QA
        """
        return await _handle_task_submit_qa(
            client, task_id, dev_notes, handoff_summary, agent_id
        )

    @mcp.tool()
    async def roboco_task_qa_pass(
        task_id: str,
        qa_notes: str,
    ) -> dict[str, Any]:
        """
        Pass a task through QA (QA role only).

        ENFORCEMENT:
        - Caller must have QA role
        - Task must be in 'awaiting_qa' status
        - QA notes required

        Args:
            task_id: The task UUID
            qa_notes: QA review notes

        Returns:
            Task ready for documentation
        """
        return await _handle_task_qa_pass(client, task_id, qa_notes, agent_id)

    @mcp.tool()
    async def roboco_task_qa_fail(
        task_id: str,
        qa_notes: str,
        issues: list[str],
    ) -> dict[str, Any]:
        """
        Fail a task in QA review (QA role only).

        ENFORCEMENT:
        - Caller must have QA role
        - Task must be in 'awaiting_qa' status
        - Issues list required

        Args:
            task_id: The task UUID
            qa_notes: QA review notes
            issues: List of specific issues found

        Returns:
            Task returned for revision
        """
        return await _handle_task_qa_fail(client, task_id, qa_notes, issues, agent_id)

    @mcp.tool()
    async def roboco_task_docs_complete(
        task_id: str,
        doc_notes: str | None = None,
    ) -> dict[str, Any]:
        """
        Mark documentation as complete (documenter only).

        Transitions task from awaiting_documentation to awaiting_pm_review.
        The Cell PM will then review and complete the task.

        ENFORCEMENT:
        - Only documenters can use this tool
        - Task must be in 'awaiting_documentation' status
        - Cannot document your own task (self-review prevention)

        Args:
            task_id: The task UUID
            doc_notes: Optional notes about the documentation completed

        Returns:
            Task now awaiting PM review
        """
        return await _handle_docs_complete(client, task_id, agent_id, doc_notes)

    @mcp.tool()
    async def roboco_task_complete(task_id: str) -> dict[str, Any]:
        """
        Mark task as completed (PM only).

        Only PMs can complete tasks, after documenter marks docs complete.
        This is the final step in the workflow: Dev → QA → Documenter → PM.

        ENFORCEMENT:
        - Only PMs can use this tool
        - Task must be in 'awaiting_pm_review' status

        Args:
            task_id: The task UUID

        Returns:
            Completed task
        """
        return await _handle_task_complete(client, task_id, agent_id)

    @mcp.tool()
    async def roboco_agent_idle() -> dict[str, Any]:
        """
        Signal that you have no work and should go idle.

        Call this when roboco_task_scan returns no tasks.
        Your container will be terminated to save resources.
        You will be automatically respawned when new work is available.

        Returns:
            Confirmation of idle state
        """
        return await _handle_agent_idle(client, agent_id)

    # =========================================================================
    # PM DELEGATION TOOLS
    # =========================================================================

    @mcp.tool()
    async def roboco_task_create(data: TaskCreateInput) -> dict[str, Any]:
        """
        Create a new task (PM and management only).

        Use this to:
        - Create subtasks when breaking down complex work
        - Create new tasks for your team (Cell PM)
        - Create tasks for any team (Main PM, Board)

        ENFORCEMENT:
        - Only PMs and management can create tasks
        - Cell PMs can only create tasks for their own team

        Args:
            data: TaskCreateInput with title, description, acceptance_criteria,
                  team, and optional parent_task_id, assigned_to, priority, complexity

        Returns:
            Created task with next step guidance
        """
        return await _handle_task_create(client, data, agent_id)

    @mcp.tool()
    async def roboco_task_assign(
        task_id: str,
        assignee: str,
    ) -> dict[str, Any]:
        """
        Assign a task to an agent (PM and management only).

        Use this to:
        - Delegate work to team members
        - Reassign tasks to different agents
        - Hand off tasks to other PMs for their teams

        ENFORCEMENT:
        - Only PMs and management can assign tasks
        - Cell PMs can only assign within their own team
        - Task must be in a claimable state (pending/paused)

        Args:
            task_id: The task UUID to assign
            assignee: Agent slug to assign (e.g., "be-dev-1", "fe-pm", "main-pm")

        Returns:
            Updated task with assignment confirmation
        """
        input_data = TaskAssignInput(task_id=task_id, assignee=assignee)
        return await _handle_task_assign(client, input_data, agent_id)

    @mcp.tool()
    async def roboco_task_cancel(
        task_id: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """
        Cancel a task (PM and board only).

        Use this to:
        - Cancel obsolete or duplicate tasks
        - Cancel tasks that are no longer needed
        - Cancel blocked tasks that cannot be resolved

        ENFORCEMENT:
        - Only PMs and board members can cancel tasks
        - CEO and Auditor cannot cancel (they observe only)
        - Cannot cancel completed or already-cancelled tasks

        Args:
            task_id: The task UUID to cancel
            reason: Optional reason for cancellation

        Returns:
            Cancelled task confirmation
        """
        return await _handle_task_cancel(client, task_id, agent_id, reason)

    @mcp.tool()
    async def roboco_task_escalate(
        task_id: str,
        reason: str,
        escalate_to: str | None = None,
    ) -> dict[str, Any]:
        """
        Escalate a task up the management hierarchy.

        Use this when:
        - Task is blocked by something outside your control
        - You need PM guidance or decision
        - Task scope has grown beyond your authority
        - Cross-team coordination is needed

        Escalation chain:
        - Developer/QA/Doc -> Cell PM
        - Cell PM -> Main PM
        - Main PM -> Product Owner

        Args:
            task_id: The task UUID to escalate
            reason: Why this task needs escalation (be specific)
            escalate_to: Optional specific target (overrides default chain)

        Returns:
            Task with escalation confirmation
        """
        input_data = TaskEscalateInput(
            task_id=task_id,
            reason=reason,
            escalate_to=escalate_to,
        )
        return await _handle_task_escalate(client, input_data, agent_id)

    return mcp


# =============================================================================
# STANDALONE RUNNER
# =============================================================================

if __name__ == "__main__":
    import sys

    two = 2

    if len(sys.argv) < two:
        print("Usage: python task_server.py <agent_id>")
        sys.exit(1)

    agent_id_cli = sys.argv[1]
    server = create_task_mcp_server(agent_id_cli)
    server.run()
