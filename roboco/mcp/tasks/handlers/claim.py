"""
Task Claim Handler

Handler for claiming tasks.
"""

from typing import Any

from roboco.agents_config import get_agent_role
from roboco.mcp.tasks import format_task_response
from roboco.mcp.tasks.handlers._helpers import (
    check_blocking_tasks,
    fetch_task_or_error,
    get_project_context,
    resolve_agent_uuid_cached,
    validate_task_claimable,
    validate_task_ownership,
)
from roboco.mcp.utils import ApiClient, format_error_response


async def _check_active_tasks(
    client: ApiClient, exclude_task_id: str | None = None
) -> dict[str, Any] | None:
    """Check for blocking tasks. Returns error or None.

    Args:
        client: API client
        exclude_task_id: Task ID to exclude from blocking check. Used when
            the agent is claiming a task already assigned to them.
    """
    active_resp = await client.get("/tasks/my")
    if not active_resp.ok:
        return None
    active_tasks = active_resp.json()

    # Exclude the task being claimed if specified
    if exclude_task_id:
        active_tasks = [t for t in active_tasks if str(t.get("id")) != exclude_task_id]

    return check_blocking_tasks(active_tasks)


async def _is_pre_assigned_to_agent(
    task: dict[str, Any], agent_id: str, client: ApiClient
) -> bool:
    """Check if task is pre-assigned to this agent (PM assigned before claim)."""
    assigned_to = task.get("assigned_to")
    if not assigned_to:
        return False

    # Task must be pending (not yet claimed)
    if task.get("status") != "pending":
        return False

    agent_uuid = await resolve_agent_uuid_cached(agent_id, client)
    return agent_uuid is not None and str(assigned_to) == agent_uuid


async def _execute_claim(
    client: ApiClient, task_id: str, agent_id: str
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Execute the claim API call. Returns (task, None) or (None, error)."""
    claim_resp = await client.post(
        f"/tasks/{task_id}/claim",
        json={"agent_id": agent_id},
    )
    if not claim_resp.ok:
        return None, format_error_response(
            "CLAIM_FAILED", "Failed to claim task", {"api_error": claim_resp.text}
        )
    claimed: dict[str, Any] = claim_resp.json()
    return claimed, None


async def _validate_git_requirements(
    client: ApiClient, task: dict[str, Any], task_id: str
) -> dict[str, Any] | None:
    """Validate git requirements for hierarchical branching.

    Rules:
    - Must have project_id (always - validated at creation)
    - Root tasks: branch auto-created from default branch
    - Subtasks: need parent to have branch (for forking)
    """
    # Must have project (should always be true - validated at creation)
    if not task.get("project_id"):
        return format_error_response(
            "PROJECT_REQUIRED",
            "Tasks require a project for branch creation.",
            {"task_id": task_id},
            hint="Assign a project to this task.",
        )

    # If already has branch, good to go
    if task.get("branch_name"):
        return None

    # For subtasks, parent must have branch (so we can fork from it)
    parent_id = task.get("parent_task_id")
    if parent_id:
        parent_resp = await client.get(f"/tasks/{parent_id}")
        if parent_resp.ok:
            parent = parent_resp.json()
            if not parent.get("branch_name"):
                return format_error_response(
                    "PARENT_BRANCH_REQUIRED",
                    "Parent task must be claimed first to create its branch.",
                    {
                        "task_id": task_id,
                        "parent_id": parent_id,
                        "parent_title": parent.get("title", "")[:50],
                    },
                    hint="Parent task must be claimed first. Branch is auto-created.",
                )

    # Root task or parent has branch - will auto-create on claim
    return None


async def _validate_sibling_sequence(
    client: ApiClient, task: dict[str, Any]
) -> dict[str, Any] | None:
    """Ensure earlier sequence siblings are complete before claiming.

    If task has sequence=2, then sequence=1 siblings must be completed first.
    """
    parent_id = task.get("parent_task_id")
    if not parent_id:
        return None  # Root task - no siblings

    my_sequence = task.get("sequence", 0)
    if my_sequence == 0:
        return None  # First in sequence, no prior siblings

    # Get all sibling tasks (same parent)
    siblings_resp = await client.get(f"/tasks?parent_task_id={parent_id}")
    if not siblings_resp.ok:
        return None

    siblings = siblings_resp.json()

    # Check if any earlier sequence siblings are NOT complete
    terminal_statuses = ["completed", "cancelled"]
    for sibling in siblings:
        sibling_seq = sibling.get("sequence", 0)
        sibling_status = sibling.get("status")

        # Skip self
        if sibling.get("id") == task.get("id"):
            continue

        # If sibling has lower sequence and isn't complete, block
        if sibling_seq < my_sequence and sibling_status not in terminal_statuses:
            return format_error_response(
                "SEQUENCE_ORDER_VIOLATION",
                f"Cannot claim - task with sequence {sibling_seq} must complete first.",
                {
                    "your_sequence": my_sequence,
                    "blocking_task_id": sibling.get("id"),
                    "blocking_sequence": sibling_seq,
                    "blocking_status": sibling_status,
                    "blocking_title": sibling.get("title", "")[:50],
                },
                hint=f"Wait for '{sibling.get('title', '')[:30]}' to complete.",
            )

    return None


async def _run_claim_validations(
    client: ApiClient, task: dict[str, Any], task_id: str, agent_id: str
) -> dict[str, Any] | None:
    """Run all claim validations. Returns error dict or None if all pass."""
    # Check if this task is pre-assigned to the agent
    is_pre_assigned = await _is_pre_assigned_to_agent(task, agent_id, client)

    # Check for blocking tasks (exclude this task if pre-assigned)
    exclude_id = task_id if is_pre_assigned else None
    if error := await _check_active_tasks(client, exclude_task_id=exclude_id):
        return error

    # Validate task can be claimed by this role
    agent_role = get_agent_role(agent_id)
    if error := await validate_task_claimable(task, agent_role, agent_id, client):
        return error

    # Validate git requirements (project exists, parent has branch if subtask)
    if error := await _validate_git_requirements(client, task, task_id):
        return error

    # Validate sibling sequence order (earlier sequence must complete first)
    if error := await _validate_sibling_sequence(client, task):
        return error

    return None


async def handle_task_claim(
    client: ApiClient, task_id: str, agent_id: str
) -> dict[str, Any]:
    """Handle task claiming.

    Flow:
    1. Fetch the task first
    2. Run validations (blocking tasks, role, git, sequence)
    3. Execute claim
    """
    # Fetch task first
    task, error = await fetch_task_or_error(client, task_id)
    if error:
        return error
    assert task is not None

    # Run all validations
    if error := await _run_claim_validations(client, task, task_id, agent_id):
        return error

    # Execute the claim
    claimed_task, error = await _execute_claim(client, task_id, agent_id)
    if error:
        return error
    assert claimed_task is not None

    project = None
    if claimed_task.get("project_id"):
        project = await get_project_context(client, claimed_task["project_id"])

    # Context-aware guidance based on task state
    guidance = _build_claim_guidance(claimed_task, task)
    next_step = "REVIEW" if claimed_task.get("plan") else "PLAN"

    return format_task_response(
        claimed_task,
        next_step,
        guidance,
        project=project,
    )


def _build_claim_guidance(claimed_task: dict, original_task: dict) -> str:
    """Build context-aware guidance based on task's previous state."""
    original_status = original_task.get("status", "pending")
    has_plan = claimed_task.get("plan")
    qa_notes = claimed_task.get("qa_notes")
    dev_notes = claimed_task.get("dev_notes")
    checkpoints = claimed_task.get("checkpoints", [])
    progress_updates = claimed_task.get("progress_updates", [])

    # NEEDS_REVISION: Task was rejected by QA or CEO - READ FEEDBACK FIRST
    if original_status == "needs_revision":
        parts = [
            "⚠️ REVISION REQUIRED - READ EXISTING CONTEXT FIRST!\n",
            "This task was REJECTED and needs fixes. Before doing anything:\n",
        ]
        if qa_notes:
            parts.append(f"1. READ QA FEEDBACK: {qa_notes[:200]}...\n")
        if has_plan:
            plan_steps = has_plan.get("steps", [])
            completed = sum(1 for s in plan_steps if s.get("completed"))
            parts.append(
                f"2. REVIEW EXISTING PLAN: {completed}/{len(plan_steps)} "
                "steps completed\n"
            )
        if dev_notes:
            parts.append("3. CHECK DEV NOTES for previous work context\n")
        parts.append(
            "\nFix the specific issues mentioned, don't restart from scratch.\n"
            "Call roboco_task_start() to resume work."
        )
        return "".join(parts)

    # Task with existing plan (resumed/paused/etc)
    if has_plan:
        plan_steps = has_plan.get("steps", [])
        completed = sum(1 for s in plan_steps if s.get("completed"))
        parts = [
            "📋 EXISTING PLAN FOUND - REVIEW BEFORE CONTINUING!\n",
            f"Plan progress: {completed}/{len(plan_steps)} steps completed\n",
        ]
        if checkpoints:
            latest_cp = checkpoints[-1]
            parts.append(f"Last checkpoint: {latest_cp.get('state_summary', 'N/A')}\n")
        if progress_updates:
            latest_prog = progress_updates[-1]
            parts.append(
                f"Last progress: {latest_prog.get('percentage', 0)}% - "
                f"{latest_prog.get('message', 'N/A')}\n"
            )
        parts.append(
            "\nREVIEW the plan and continue from where work stopped.\n"
            "Call roboco_task_start() to resume work."
        )
        return "".join(parts)

    # Fresh task - no plan yet
    return (
        "Task claimed. NEXT: Call roboco_task_plan() before you can start.\n"
        "1. Read the description and acceptance criteria\n"
        "2. Ask questions if anything is unclear\n"
        "3. Call roboco_task_plan(task_id, approach, steps)\n"
        "4. Then call roboco_task_start(task_id)"
    )


async def handle_task_unclaim(
    client: ApiClient, task_id: str, agent_id: str, hand_off_to: str | None = None
) -> dict[str, Any]:
    """Handle releasing a claimed task back to the pool.

    Use this when you claimed a task but realize you shouldn't work on it.
    Optionally specify another agent to hand off to.

    Args:
        client: API client
        task_id: Task to unclaim
        agent_id: Agent releasing the task
        hand_off_to: Optional agent slug to assign the task to
    """
    # Fetch task first
    task, error = await fetch_task_or_error(client, task_id)
    if error:
        return error
    assert task is not None

    # Validate ownership
    if error := await validate_task_ownership(task, agent_id, client):
        return error

    # Validate status
    if task.get("status") != "claimed":
        return format_error_response(
            "INVALID_STATE",
            "Can only unclaim tasks in 'claimed' status. "
            f"Current: '{task.get('status')}'",
            hint="If task is in_progress, use roboco_task_pause() instead.",
        )

    # Build request payload
    payload: dict[str, Any] = {}
    if hand_off_to:
        payload["agent_id"] = hand_off_to

    # Execute unclaim
    resp = await client.post(f"/tasks/{task_id}/unclaim", json=payload or None)

    if not resp.ok:
        return format_error_response(
            "UNCLAIM_FAILED",
            f"Failed to unclaim task: {resp.text}",
            {"status_code": resp.status_code},
        )

    result = resp.json()

    if hand_off_to:
        guidance = (
            f"Task released and handed off to {hand_off_to}.\n"
            "They can now claim and work on it.\n"
            "Call roboco_task_scan() to find your next task."
        )
    else:
        guidance = (
            "Task released back to the pool.\n"
            "It can now be claimed by any eligible agent.\n"
            "Call roboco_task_scan() to find your next task."
        )

    return format_task_response(result, "SCAN_FOR_WORK", guidance)
