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
- roboco_task_complete: Mark task complete
- roboco_task_create: Create new task (PM only)
- roboco_task_assign: Assign task to agent (PM only)
- roboco_task_escalate: Escalate task up hierarchy (all agents)
"""

from typing import Any

import httpx
from fastapi import status
from mcp.server.fastmcp import FastMCP

from roboco.agents_config import (
    can_assign_tasks,
    can_create_tasks,
    get_agent_role,
    get_agent_team,
    get_escalation_target,
)
from roboco.config import settings
from roboco.llm import ToonAdapter
from roboco.mcp.schemas import TaskAssignInput, TaskCreateInput, TaskEscalateInput
from roboco.services.task import extract_original_developer


def _get_agent_headers(agent_id: str) -> dict[str, str]:
    """Get standard headers for API calls."""
    headers = {
        "X-Agent-ID": agent_id,
        "X-Agent-Role": get_agent_role(agent_id),
    }
    team = get_agent_team(agent_id)
    if team:
        headers["X-Agent-Team"] = team
    return headers


# Cache for agent slug -> UUID resolution
_agent_uuid_cache: dict[str, str] = {}


async def _resolve_agent_uuid(agent_id: str, headers: dict[str, str]) -> str | None:
    """Resolve agent slug to UUID. Returns None if not found."""
    # Check if already a valid UUID
    try:
        from uuid import UUID

        UUID(agent_id)
        return agent_id  # Already a UUID
    except ValueError:
        pass

    # Check cache
    if agent_id in _agent_uuid_cache:
        return _agent_uuid_cache[agent_id]

    # Query API to resolve slug to UUID
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{settings.internal_api_url}/agents",
            params={"slug": agent_id},
            headers=headers,
        )
        if resp.status_code == status.HTTP_200_OK:
            agents = resp.json()
            if agents:
                uuid_str = str(agents[0]["id"])
                _agent_uuid_cache[agent_id] = uuid_str
                return uuid_str

    return None


# Global TOON adapter for encoding task data
_toon = ToonAdapter()

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


def _format_error_response(
    error_code: str,
    message: str,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Format a standardized error response."""
    return {
        "error": {
            "code": error_code,
            "message": message,
            "details": details or {},
        }
    }


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


async def _handle_task_scan(team: str | None, agent_id: str) -> dict[str, Any]:
    """Handle task scanning."""
    headers = _get_agent_headers(agent_id)
    async with httpx.AsyncClient() as client:
        paused_resp = await client.get(
            f"{settings.internal_api_url}/tasks/my",
            params={"status": "paused"},
            headers=headers,
        )
        paused_tasks = (
            paused_resp.json() if paused_resp.status_code == status.HTTP_200_OK else []
        )

        # Get assigned tasks using /tasks/my
        # Includes: PM-assigned pending tasks + tasks being actively worked on
        assigned_resp = await client.get(
            f"{settings.internal_api_url}/tasks/my",
            headers=headers,
        )
        assigned_data = (
            assigned_resp.json()
            if assigned_resp.status_code == status.HTTP_200_OK
            else []
        )
        # Include pending tasks (PM assigned) + active work statuses
        assigned_tasks = [
            t
            for t in assigned_data
            if t.get("status")
            in ["pending", "claimed", "in_progress", "verifying", "needs_revision"]
        ]

        # Get available tasks based on agent role
        # QA agents need awaiting_qa tasks, Documenters need awaiting_documentation
        agent_role = get_agent_role(agent_id)

        available_tasks: list[dict[str, Any]] = []

        if agent_role == "qa":
            # QA agents look for tasks awaiting QA review
            qa_resp = await client.get(
                f"{settings.internal_api_url}/tasks/awaiting-qa",
                params={"team": team} if team else {},
                headers=headers,
            )
            if qa_resp.status_code == status.HTTP_200_OK:
                available_tasks = qa_resp.json()
        elif agent_role == "documenter":
            # Documenters look for tasks awaiting documentation
            doc_resp = await client.get(
                f"{settings.internal_api_url}/tasks/awaiting-docs",
                params={"team": team} if team else {},
                headers=headers,
            )
            if doc_resp.status_code == status.HTTP_200_OK:
                available_tasks = doc_resp.json()
        else:
            # Developers and PMs look for pending tasks
            params: dict[str, Any] = {"status": "pending"}
            if team:
                params["team"] = team
            pending_resp = await client.get(
                f"{settings.internal_api_url}/tasks",
                params=params,
                headers=headers,
            )
            if pending_resp.status_code == status.HTTP_200_OK:
                available_tasks = pending_resp.json()

    # Filter out tasks already in assigned_tasks from available_tasks
    # (prevents PM-assigned pending tasks from appearing in both lists)
    assigned_ids = {t.get("id") for t in assigned_tasks}
    available_tasks = [t for t in available_tasks if t.get("id") not in assigned_ids]

    # Determine guidance
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
        guidance = (
            f"Found {len(available_tasks)} available task(s). "
            "Review and claim one that matches your skills."
        )
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


async def _handle_task_get(task_id: str, agent_id: str) -> dict[str, Any]:
    """Handle getting task details."""
    headers = _get_agent_headers(agent_id)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{settings.internal_api_url}/tasks/{task_id}",
            headers=headers,
        )

        if resp.status_code == status.HTTP_404_NOT_FOUND:
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


async def _get_project_context(project_id: str, agent_id: str) -> dict[str, Any] | None:
    """Fetch project context if available."""
    headers = _get_agent_headers(agent_id)
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{settings.internal_api_url}/projects/{project_id}",
            headers=headers,
        )
        if resp.status_code == status.HTTP_200_OK:
            result: dict[str, Any] = resp.json()
            return result
    return None


async def _handle_task_claim(task_id: str, agent_id: str) -> dict[str, Any]:
    """Handle task claiming."""
    headers = _get_agent_headers(agent_id)
    async with httpx.AsyncClient() as client:
        active_resp = await client.get(
            f"{settings.internal_api_url}/tasks/my",
            headers=headers,
        )
        if active_resp.status_code == status.HTTP_200_OK:
            active_tasks = active_resp.json()
            if error := _check_blocking_tasks(active_tasks):
                return error
            if error := _check_paused_tasks(active_tasks):
                return error

        task_resp = await client.get(
            f"{settings.internal_api_url}/tasks/{task_id}",
            headers=headers,
        )
        if task_resp.status_code == status.HTTP_404_NOT_FOUND:
            return _format_error_response("NOT_FOUND", f"Task {task_id} not found")

        task = task_resp.json()
        agent_role = get_agent_role(agent_id)
        if error := _validate_task_claimable(task, agent_role):
            return error

        claim_resp = await client.post(
            f"{settings.internal_api_url}/tasks/{task_id}/claim",
            json={"agent_id": agent_id},
            headers=headers,
        )
        if claim_resp.status_code != status.HTTP_200_OK:
            return _format_error_response(
                "CLAIM_FAILED",
                "Failed to claim task",
                {"api_error": claim_resp.text},
            )

        claimed_task = claim_resp.json()

    project = None
    if claimed_task.get("project_id"):
        project = await _get_project_context(claimed_task["project_id"], agent_id)

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
    task: dict, agent_id: str, headers: dict[str, str]
) -> dict[str, Any] | None:
    """Validate agent owns the task. Returns error or None."""
    assigned_to = task.get("assigned_to")
    if not assigned_to:
        return _format_error_response(
            "NOT_ASSIGNED",
            "This task is not assigned to anyone",
        )

    # Resolve agent_id (which may be a slug) to UUID for comparison
    agent_uuid = await _resolve_agent_uuid(agent_id, headers)
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
    task_id: str,
    plan_params: dict[str, Any],
    agent_id: str,
) -> dict[str, Any]:
    """Handle task planning."""
    headers = _get_agent_headers(agent_id)
    async with httpx.AsyncClient() as client:
        task_resp = await client.get(
            f"{settings.internal_api_url}/tasks/{task_id}",
            headers=headers,
        )
        if task_resp.status_code == status.HTTP_404_NOT_FOUND:
            return _format_error_response("NOT_FOUND", f"Task {task_id} not found")

        task = task_resp.json()
        if error := await _validate_task_ownership(task, agent_id, headers):
            return error
        if error := _validate_task_status_claimed(task):
            return error

        plan_data = _build_plan_data(plan_params)
        update_resp = await client.patch(
            f"{settings.internal_api_url}/tasks/{task_id}",
            json={"plan": plan_data},
            headers=headers,
        )
        if update_resp.status_code != status.HTTP_200_OK:
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
    task: dict[str, Any], agent_id: str, headers: dict[str, str]
) -> dict[str, Any] | None:
    """Validate task can be started. Returns error dict or None."""
    if error := await _validate_task_ownership(task, agent_id, headers):
        return error

    task_status = task.get("status")
    if task_status not in ["claimed", "paused"]:
        return _format_error_response(
            "INVALID_STATE",
            f"Cannot start task in '{task_status}' status. "
            "Task must be 'claimed' or 'paused'.",
            {"current_status": task_status},
        )

    if task_status == "claimed" and not task.get("plan"):
        return _format_error_response(
            "NO_PLAN",
            "Cannot start without a plan. Call roboco_task_plan first.",
        )

    plan = task.get("plan", {})
    unanswered = [q for q in plan.get("open_questions", []) if not q.get("answered")]
    if unanswered:
        return _format_error_response(
            "UNANSWERED_QUESTIONS",
            f"Cannot start with {len(unanswered)} "
            "unanswered question(s). "
            "Get answers first, then update the plan.",
            {"questions": [q.get("question") for q in unanswered]},
        )

    return None


async def _handle_task_start(task_id: str, agent_id: str) -> dict[str, Any]:
    """Handle task start."""
    headers = _get_agent_headers(agent_id)
    async with httpx.AsyncClient() as client:
        task_resp = await client.get(
            f"{settings.internal_api_url}/tasks/{task_id}",
            headers=headers,
        )
        if task_resp.status_code == status.HTTP_404_NOT_FOUND:
            return _format_error_response("NOT_FOUND", f"Task {task_id} not found")

        task = task_resp.json()

        if validation_error := await _validate_task_start(task, agent_id, headers):
            return validation_error

        # Start the task
        start_resp = await client.post(
            f"{settings.internal_api_url}/tasks/{task_id}/start",
            headers=headers,
        )

        if start_resp.status_code != status.HTTP_200_OK:
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
    task_id: str,
    message: str,
    percentage: int | None,
    agent_id: str,
) -> dict[str, Any]:
    """Handle task progress update."""
    headers = _get_agent_headers(agent_id)
    async with httpx.AsyncClient() as client:
        task_resp = await client.get(
            f"{settings.internal_api_url}/tasks/{task_id}",
            headers=headers,
        )
        if task_resp.status_code == status.HTTP_404_NOT_FOUND:
            return _format_error_response("NOT_FOUND", f"Task {task_id} not found")

        task = task_resp.json()

        if error := await _validate_task_ownership(task, agent_id, headers):
            return error

        if task.get("status") != "in_progress":
            return _format_error_response(
                "INVALID_STATE",
                "Can only update progress for in_progress tasks",
            )

        # Add progress update
        progress_resp = await client.post(
            f"{settings.internal_api_url}/tasks/{task_id}/progress",
            json={
                "agent_id": agent_id,
                "message": message,
                "percentage": percentage,
            },
            headers=headers,
        )

        if progress_resp.status_code != status.HTTP_200_OK:
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
    task_id: str,
    reason: str,
    blocker_type: str,
    what_needed: str,
    agent_id: str,
) -> dict[str, Any]:
    """Handle task blocking."""
    if not reason or not what_needed:
        return _format_error_response(
            "MISSING_DETAILS",
            "Both 'reason' and 'what_needed' are required to block a task.",
        )

    headers = _get_agent_headers(agent_id)
    async with httpx.AsyncClient() as client:
        task_resp = await client.get(
            f"{settings.internal_api_url}/tasks/{task_id}",
            headers=headers,
        )
        if task_resp.status_code == status.HTTP_404_NOT_FOUND:
            return _format_error_response("NOT_FOUND", f"Task {task_id} not found")

        task = task_resp.json()

        if error := await _validate_task_ownership(task, agent_id, headers):
            return error

        if task.get("status") != "in_progress":
            return _format_error_response(
                "INVALID_STATE",
                "Can only block in_progress tasks",
            )

        # Build blocker note for dev_notes
        blocker_note = (
            f"[BLOCKED - {blocker_type.upper()}]\n"
            f"Reason: {reason}\n"
            f"What's needed: {what_needed}"
        )
        existing_notes = task.get("dev_notes") or ""
        if existing_notes:
            updated_notes = f"{existing_notes}\n\n{blocker_note}"
        else:
            updated_notes = blocker_note

        # Block the task using PATCH to update status and notes
        block_resp = await client.patch(
            f"{settings.internal_api_url}/tasks/{task_id}",
            json={
                "status": "blocked",
                "dev_notes": updated_notes,
            },
            headers=headers,
        )

        if block_resp.status_code != status.HTTP_200_OK:
            return _format_error_response(
                "BLOCK_FAILED",
                "Failed to block task",
                {"status_code": block_resp.status_code, "detail": block_resp.text},
            )

        blocked_task = block_resp.json()

    return _format_task_response(
        blocked_task,
        "WAIT_OR_SWITCH",
        f"Task blocked: {reason}\n\n"
        "Options:\n"
        "1. WAIT - If resolution expected soon, poll for updates\n"
        "2. SWITCH - Call roboco_task_scan to work on another task\n"
        "3. ESCALATE - Message your PM if this is urgent\n\n"
        "The blocker has been communicated. "
        "You'll be notified when resolved.",
    )


async def _handle_task_unblock(task_id: str, agent_id: str) -> dict[str, Any]:
    """Handle task unblocking."""
    headers = _get_agent_headers(agent_id)
    async with httpx.AsyncClient() as client:
        task_resp = await client.get(
            f"{settings.internal_api_url}/tasks/{task_id}",
            headers=headers,
        )
        if task_resp.status_code == status.HTTP_404_NOT_FOUND:
            return _format_error_response("NOT_FOUND", f"Task {task_id} not found")

        task = task_resp.json()

        if error := await _validate_task_ownership(task, agent_id, headers):
            return error

        if task.get("status") != "blocked":
            return _format_error_response(
                "INVALID_STATE",
                "Task is not blocked",
            )

        unblock_resp = await client.post(
            f"{settings.internal_api_url}/tasks/{task_id}/unblock",
            headers=headers,
        )

        if unblock_resp.status_code != status.HTTP_200_OK:
            return _format_error_response("UNBLOCK_FAILED", "Failed to unblock task")

        unblocked_task = unblock_resp.json()

    return _format_task_response(
        unblocked_task,
        "CONTINUE",
        "Task unblocked. Resume from your last checkpoint.",
    )


async def _handle_task_pause(
    task_id: str,
    reason: str,
    checkpoint_summary: str,
    remaining_work: list[str],
    agent_id: str,
) -> dict[str, Any]:
    """Handle task pausing."""
    headers = _get_agent_headers(agent_id)
    async with httpx.AsyncClient() as client:
        task_resp = await client.get(
            f"{settings.internal_api_url}/tasks/{task_id}",
            headers=headers,
        )
        if task_resp.status_code == status.HTTP_404_NOT_FOUND:
            return _format_error_response("NOT_FOUND", f"Task {task_id} not found")

        task = task_resp.json()

        if error := await _validate_task_ownership(task, agent_id, headers):
            return error

        if task.get("status") != "in_progress":
            return _format_error_response(
                "INVALID_STATE",
                "Can only pause in_progress tasks",
            )

        # Add checkpoint
        await client.post(
            f"{settings.internal_api_url}/tasks/{task_id}/checkpoint",
            json={
                "agent_id": agent_id,
                "state_summary": checkpoint_summary,
                "remaining_work": remaining_work,
                "notes": reason,
            },
            headers=headers,
        )

        # Pause the task
        pause_resp = await client.post(
            f"{settings.internal_api_url}/tasks/{task_id}/pause",
            headers=headers,
        )

        if pause_resp.status_code != status.HTTP_200_OK:
            return _format_error_response("PAUSE_FAILED", "Failed to pause task")

        paused_task = pause_resp.json()

    return _format_task_response(
        paused_task,
        "SCAN_FOR_WORK",
        f"Task paused. Checkpoint saved.\n"
        f"Reason: {reason}\n\n"
        "To resume later, call roboco_task_start with this task_id.\n"
        "Now call roboco_task_scan to find your next task.",
    )


async def _handle_task_submit_verification(
    task_id: str, agent_id: str
) -> dict[str, Any]:
    """Handle task verification submission."""
    headers = _get_agent_headers(agent_id)
    async with httpx.AsyncClient() as client:
        task_resp = await client.get(
            f"{settings.internal_api_url}/tasks/{task_id}",
            headers=headers,
        )
        if task_resp.status_code == status.HTTP_404_NOT_FOUND:
            return _format_error_response("NOT_FOUND", f"Task {task_id} not found")

        task = task_resp.json()

        if error := await _validate_task_ownership(task, agent_id, headers):
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

        verify_resp = await client.post(
            f"{settings.internal_api_url}/tasks/{task_id}/verify",
            headers=headers,
        )

        if verify_resp.status_code != status.HTTP_200_OK:
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
    task_id: str,
    dev_notes: str,
    handoff_summary: str,
    agent_id: str,
) -> dict[str, Any]:
    """Handle task QA submission."""
    if not dev_notes or not handoff_summary:
        return _format_error_response(
            "MISSING_NOTES",
            "Both dev_notes and handoff_summary are required for QA submission.",
        )

    headers = _get_agent_headers(agent_id)
    async with httpx.AsyncClient() as client:
        task_resp = await client.get(
            f"{settings.internal_api_url}/tasks/{task_id}",
            headers=headers,
        )
        if task_resp.status_code == status.HTTP_404_NOT_FOUND:
            return _format_error_response("NOT_FOUND", f"Task {task_id} not found")

        task = task_resp.json()

        if error := await _validate_task_ownership(task, agent_id, headers):
            return error

        if task.get("status") != "verifying":
            return _format_error_response(
                "INVALID_STATE",
                "Can only submit verified tasks for QA",
            )

        # Update with notes - combine dev_notes and handoff summary
        # (handoff summary goes into dev_notes for documenter to read)
        combined_notes = f"{dev_notes}\n\n---\nHandoff Summary:\n{handoff_summary}"
        await client.patch(
            f"{settings.internal_api_url}/tasks/{task_id}",
            json={"dev_notes": combined_notes},
            headers=headers,
        )

        # Submit for QA
        qa_resp = await client.post(
            f"{settings.internal_api_url}/tasks/{task_id}/submit-qa",
            headers=headers,
        )

        if qa_resp.status_code != status.HTTP_200_OK:
            return _format_error_response("SUBMIT_FAILED", "Failed to submit for QA")

        qa_task = qa_resp.json()

    return _format_task_response(
        qa_task,
        "WAIT_FOR_QA",
        "Task submitted for QA review.\n"
        "You will be notified of the result.\n"
        "In the meantime, call roboco_task_scan for other work.",
    )


async def _handle_task_qa_pass(
    task_id: str,
    qa_notes: str,
    agent_id: str,
) -> dict[str, Any]:
    """Handle task QA pass."""
    # Check if agent has QA role (simple check - real impl would verify)
    if "qa" not in agent_id.lower():
        return _format_error_response(
            "NOT_QA",
            "Only QA agents can pass tasks through QA review.",
        )

    headers = _get_agent_headers(agent_id)
    async with httpx.AsyncClient() as client:
        task_resp = await client.get(
            f"{settings.internal_api_url}/tasks/{task_id}",
            headers=headers,
        )
        if task_resp.status_code == status.HTTP_404_NOT_FOUND:
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
        agent_uuid = await _resolve_agent_uuid(agent_id, headers)
        if original_dev and agent_uuid and agent_uuid == original_dev:
            return _format_error_response(
                "SELF_REVIEW",
                "Cannot review your own work.",
            )

        pass_resp = await client.post(
            f"{settings.internal_api_url}/tasks/{task_id}/pass-qa",
            json={"notes": qa_notes},
            headers=headers,
        )

        if pass_resp.status_code != status.HTTP_200_OK:
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
    task_id: str,
    qa_notes: str,
    issues: list[str],
    agent_id: str,
) -> dict[str, Any]:
    """Handle task QA failure."""
    if "qa" not in agent_id.lower():
        return _format_error_response(
            "NOT_QA",
            "Only QA agents can fail tasks in QA review.",
        )

    if not issues:
        return _format_error_response(
            "NO_ISSUES",
            "Must specify at least one issue when failing QA.",
        )

    headers = _get_agent_headers(agent_id)
    async with httpx.AsyncClient() as client:
        task_resp = await client.get(
            f"{settings.internal_api_url}/tasks/{task_id}",
            headers=headers,
        )
        if task_resp.status_code == status.HTTP_404_NOT_FOUND:
            return _format_error_response("NOT_FOUND", f"Task {task_id} not found")

        task = task_resp.json()

        if task.get("status") != "awaiting_qa":
            return _format_error_response(
                "INVALID_STATE",
                "Task is not awaiting QA",
            )

        full_notes = f"{qa_notes}\n\nIssues:\n" + "\n".join(f"- {i}" for i in issues)

        fail_resp = await client.post(
            f"{settings.internal_api_url}/tasks/{task_id}/fail-qa",
            json={"notes": full_notes},
            headers=headers,
        )

        if fail_resp.status_code != status.HTTP_200_OK:
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


async def _handle_task_complete(task_id: str, agent_id: str) -> dict[str, Any]:
    """Handle task completion."""
    headers = _get_agent_headers(agent_id)
    async with httpx.AsyncClient() as client:
        task_resp = await client.get(
            f"{settings.internal_api_url}/tasks/{task_id}",
            headers=headers,
        )
        if task_resp.status_code == status.HTTP_404_NOT_FOUND:
            return _format_error_response("NOT_FOUND", f"Task {task_id} not found")

        task = task_resp.json()

        if task.get("status") != "awaiting_documentation":
            return _format_error_response(
                "INVALID_STATE",
                "Task must be awaiting documentation to complete",
            )

        complete_resp = await client.post(
            f"{settings.internal_api_url}/tasks/{task_id}/complete",
            headers=headers,
        )

        if complete_resp.status_code != status.HTTP_200_OK:
            return _format_error_response(
                "COMPLETE_FAILED",
                "Failed to complete task",
                {
                    "status_code": complete_resp.status_code,
                    "api_error": complete_resp.text,
                },
            )

        completed_task = complete_resp.json()

    return _format_task_response(
        completed_task,
        "DONE",
        "Task completed successfully!\nCall roboco_task_scan for new work.",
    )


async def _handle_agent_idle(agent_id: str) -> dict[str, Any]:
    """Handle agent going idle (no work available)."""
    headers = _get_agent_headers(agent_id)

    async with httpx.AsyncClient(timeout=30.0) as client:
        # First, check if agent has any in-progress tasks
        # Use /tasks/my endpoint which properly uses authenticated agent context
        try:
            scan_resp = await client.get(
                f"{settings.internal_api_url}/tasks/my",
                params={"status": "in_progress"},
                headers=headers,
            )
            if scan_resp.status_code == status.HTTP_200_OK:
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
                f"{settings.internal_api_url}/orchestrator/agents/{agent_id}/mark-waiting",
                params={"waiting_for": "task_assignment"},
                headers=headers,
            )
        except httpx.TimeoutException:
            return _format_error_response(
                "TIMEOUT",
                "Request to mark idle timed out",
            )
        except httpx.RequestError as e:
            return _format_error_response(
                "CONNECTION_ERROR",
                f"Failed to connect to orchestrator: {type(e).__name__}",
            )

        if resp.status_code == status.HTTP_204_NO_CONTENT:
            return {
                "status": "idle",
                "message": (
                    "You are now in WAITING state. Your container will terminate "
                    "to save resources. You will be respawned when work is available."
                ),
                "action": "EXIT_GRACEFULLY",
            }

        # Handle specific error codes
        if resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
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
    input_data: TaskCreateInput,
    agent_id: str,
) -> dict[str, Any]:
    """Handle task creation by PM."""
    headers = _get_agent_headers(agent_id)
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

    async with httpx.AsyncClient(timeout=30.0) as client:
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
            create_resp = await client.post(
                f"{settings.internal_api_url}/tasks",
                json=payload,
                headers=headers,
            )
        except httpx.RequestError as e:
            return _format_error_response(
                "CONNECTION_ERROR",
                f"Failed to connect to API: {type(e).__name__}",
            )

        if create_resp.status_code != status.HTTP_201_CREATED:
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
                client, task["id"], input_data.assigned_to, headers
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
    client: httpx.AsyncClient,
    task_id: str,
    headers: dict[str, str],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Fetch task for assignment. Returns (task, error) tuple."""
    try:
        task_resp = await client.get(
            f"{settings.internal_api_url}/tasks/{task_id}",
            headers=headers,
        )
    except httpx.RequestError as e:
        return None, _format_error_response(
            "CONNECTION_ERROR",
            f"Failed to connect to API: {type(e).__name__}",
        )

    if task_resp.status_code == status.HTTP_404_NOT_FOUND:
        return None, _format_error_response("NOT_FOUND", f"Task {task_id} not found")

    if task_resp.status_code != status.HTTP_200_OK:
        return None, _format_error_response(
            "FETCH_FAILED",
            "Failed to fetch task",
            {"status_code": task_resp.status_code},
        )

    return task_resp.json(), None


async def _assign_task_to_agent(
    client: httpx.AsyncClient,
    task_id: str,
    assignee: str,
    headers: dict[str, str],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """
    Assign task to agent by setting assigned_to and resetting to pending.

    This is different from claiming - assignment means "this agent should work
    on this task". The orchestrator will then spawn the agent who will claim it.

    Returns (assigned_task, error) tuple.
    """
    # Resolve assignee slug to UUID
    assignee_id = await _resolve_agent_uuid(assignee, headers)
    if not assignee_id:
        return None, _format_error_response(
            "INVALID_ASSIGNEE",
            f"Could not resolve agent: {assignee}",
            {"assignee": assignee},
        )

    try:
        # PATCH to set assigned_to and reset status to pending
        assign_resp = await client.patch(
            f"{settings.internal_api_url}/tasks/{task_id}",
            json={"assigned_to": assignee_id, "status": "pending"},
            headers=headers,
        )
    except httpx.RequestError as e:
        return None, _format_error_response(
            "CONNECTION_ERROR",
            f"Failed to connect to API: {type(e).__name__}",
        )

    if assign_resp.status_code != status.HTTP_200_OK:
        return None, _format_error_response(
            "ASSIGN_FAILED",
            "Failed to assign task",
            {"status_code": assign_resp.status_code, "detail": assign_resp.text},
        )

    return assign_resp.json(), None


async def _handle_task_assign(
    input_data: TaskAssignInput,
    agent_id: str,
) -> dict[str, Any]:
    """Handle task assignment by PM."""
    headers = _get_agent_headers(agent_id)
    agent_team = get_agent_team(agent_id)
    role = get_agent_role(agent_id)

    # Validate PM role
    if not can_assign_tasks(agent_id):
        return _format_error_response(
            "PERMISSION_DENIED",
            "Only PMs and management can assign tasks",
            {"role": role},
        )

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Get task details first
        task, error = await _fetch_task_for_assignment(
            client, input_data.task_id, headers
        )
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
            client, input_data.task_id, input_data.assignee, headers
        )
        if assign_error or assigned_task is None:
            return assign_error or _format_error_response("ASSIGN_FAILED", "No task")

    guidance = (
        f"Task assigned to {input_data.assignee} and set to pending. "
        "Orchestrator will spawn them to claim and work on it."
    )
    return _format_task_response(assigned_task, "ASSIGNED", guidance)


async def _handle_task_escalate(
    input_data: TaskEscalateInput,
    agent_id: str,
) -> dict[str, Any]:
    """Handle task escalation up the hierarchy."""
    headers = _get_agent_headers(agent_id)

    # Determine and resolve escalation target upfront
    target = input_data.escalate_to or get_escalation_target(agent_id)
    if not target:
        return _format_error_response(
            "NO_ESCALATION_PATH",
            f"No escalation path from agent: {agent_id}",
            {"role": get_agent_role(agent_id)},
        )

    target_uuid = await _resolve_agent_uuid(target, headers)
    if not target_uuid:
        return _format_error_response(
            "INVALID_TARGET",
            f"Could not resolve escalation target: {target}",
        )

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            # Get task details
            task_resp = await client.get(
                f"{settings.internal_api_url}/tasks/{input_data.task_id}",
                headers=headers,
            )

            if task_resp.status_code == status.HTTP_404_NOT_FOUND:
                return _format_error_response(
                    "NOT_FOUND", f"Task {input_data.task_id} not found"
                )

            task = task_resp.json()

            # Create escalation notification
            notif_resp = await client.post(
                f"{settings.internal_api_url}/notifications",
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
                headers=headers,
            )

            if notif_resp.status_code not in (
                status.HTTP_200_OK,
                status.HTTP_201_CREATED,
            ):
                return _format_error_response(
                    "ESCALATION_FAILED",
                    "Failed to send escalation notification",
                    {"status_code": notif_resp.status_code, "detail": notif_resp.text},
                )
    except httpx.RequestError as e:
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
        return await _handle_task_scan(team, agent_id)

    @mcp.tool()
    async def roboco_task_get(task_id: str) -> dict[str, Any]:
        """
        Get detailed information about a task.

        Args:
            task_id: The task UUID

        Returns:
            Task details with current status and guidance
        """
        return await _handle_task_get(task_id, agent_id)

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
        return await _handle_task_claim(task_id, agent_id)

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
        return await _handle_task_plan(task_id, plan_params, agent_id)

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
        return await _handle_task_start(task_id, agent_id)

    @mcp.tool()
    async def roboco_task_progress(
        task_id: str,
        message: str,
        percentage: int | None = None,
    ) -> dict[str, Any]:
        """
        Update task progress.

        Args:
            task_id: The task UUID
            message: Progress update message
            percentage: Optional completion percentage (0-100)

        Returns:
            Updated task
        """
        return await _handle_task_progress(task_id, message, percentage, agent_id)

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
        return await _handle_task_block(
            task_id, reason, blocker_type, what_needed, agent_id
        )

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
        return await _handle_task_unblock(task_id, agent_id)

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
        return await _handle_task_pause(
            task_id, reason, checkpoint_summary, remaining_work, agent_id
        )

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
        return await _handle_task_submit_verification(task_id, agent_id)

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
            task_id, dev_notes, handoff_summary, agent_id
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
        return await _handle_task_qa_pass(task_id, qa_notes, agent_id)

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
        return await _handle_task_qa_fail(task_id, qa_notes, issues, agent_id)

    @mcp.tool()
    async def roboco_task_complete(task_id: str) -> dict[str, Any]:
        """
        Mark task as completed (typically by Documenter).

        ENFORCEMENT:
        - Task must be in 'awaiting_documentation' status
        - Documentation must exist

        Args:
            task_id: The task UUID

        Returns:
            Completed task
        """
        return await _handle_task_complete(task_id, agent_id)

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
        return await _handle_agent_idle(agent_id)

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
        return await _handle_task_create(data, agent_id)

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
        return await _handle_task_assign(input_data, agent_id)

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
        return await _handle_task_escalate(input_data, agent_id)

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
