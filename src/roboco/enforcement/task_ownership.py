"""
Task Ownership Enforcement

Validates task ownership and claim rules.
"""

from roboco.agents_config import get_agent_role, get_agent_team
from roboco.enforcement.task_lifecycle import is_waiting_state
from roboco.exceptions import RobocoError


class TaskOwnershipError(RobocoError):
    """Raised when a task ownership rule is violated."""

    def __init__(
        self,
        agent_id: str,
        task_id: str,
        action: str,
        message: str | None = None,
    ):
        self.agent_id = agent_id
        self.task_id = task_id
        self.action = action
        super().__init__(
            code="TASK_OWNERSHIP_ERROR",
            message=message or f"Agent {agent_id} cannot {action} task {task_id}",
            details={
                "agent_id": agent_id,
                "task_id": task_id,
                "action": action,
            },
        )


def validate_task_ownership(
    agent_id: str,
    task_id: str,
    task_assigned_to: str | None,
    task_team: str,
    action: str,
) -> bool:
    """
    Validate agent can perform action on task.

    Rules:
    - Only assigned agent can modify task content/status
    - Paused/blocked tasks stay with original owner
    - PMs can reassign (but not modify content)
    - Cell PMs can only work with their cell's tasks

    Args:
        agent_id: The agent attempting the action
        task_id: The task ID
        task_assigned_to: Current assignee (None if unassigned)
        task_team: The team that owns the task
        action: The action being attempted

    Returns:
        True if allowed

    Raises:
        TaskOwnershipError: If not allowed
    """
    role = get_agent_role(agent_id)
    agent_team = get_agent_team(agent_id)

    # CLAIM action - task must be unassigned
    if action == "claim":
        if task_assigned_to is not None:
            raise TaskOwnershipError(
                agent_id=agent_id,
                task_id=task_id,
                action=action,
                message=f"Task is already assigned to {task_assigned_to}",
            )
        return True

    # REASSIGN action - only PMs
    if action == "reassign":
        if role not in ("cell_pm", "main_pm"):
            raise TaskOwnershipError(
                agent_id=agent_id,
                task_id=task_id,
                action=action,
                message="Only PMs can reassign tasks",
            )
        # Cell PM can only reassign within their cell
        if role == "cell_pm" and agent_team != task_team:
            raise TaskOwnershipError(
                agent_id=agent_id,
                task_id=task_id,
                action=action,
                message=f"Cell PM can only reassign tasks in their cell ({agent_team})",
            )
        return True

    # VIEW action - generally allowed, but can add restrictions
    if action == "view":
        return True

    # All other actions - must be assigned to the agent
    if task_assigned_to != agent_id:
        raise TaskOwnershipError(
            agent_id=agent_id,
            task_id=task_id,
            action=action,
            message=f"Task is assigned to {task_assigned_to}, not you",
        )

    return True


def validate_task_claim(
    agent_id: str,
    task_id: str,
    task_status: str,
    task_team: str,
    agent_active_tasks: list[dict],
    agent_paused_tasks: list[dict],
) -> bool:
    """
    Validate agent can claim a specific task.

    Rules:
    - Task must be in 'pending' status
    - Agent cannot have an active task (claimed, in_progress, verifying)
    - Agent must resume paused tasks before claiming new ones
    - Agent should be in the same team as the task (warning, not error)

    Args:
        agent_id: The agent attempting to claim
        task_id: The task to claim
        task_status: Current task status
        task_team: Task's team
        agent_active_tasks: Agent's current active tasks
        agent_paused_tasks: Agent's paused tasks

    Returns:
        True if can claim

    Raises:
        TaskOwnershipError: If cannot claim
    """
    # Check task is pending
    if task_status != "pending":
        raise TaskOwnershipError(
            agent_id=agent_id,
            task_id=task_id,
            action="claim",
            message=f"Cannot claim task in '{task_status}' status. Only 'pending' tasks can be claimed.",
        )

    # Check for paused tasks
    if agent_paused_tasks:
        paused_ids = [t.get("id") for t in agent_paused_tasks]
        raise TaskOwnershipError(
            agent_id=agent_id,
            task_id=task_id,
            action="claim",
            message=f"You have {len(agent_paused_tasks)} paused task(s). "
            f"Resume paused work before claiming new tasks. Paused: {paused_ids}",
        )

    # Check for active tasks
    active = [
        t for t in agent_active_tasks if not is_waiting_state(t.get("status", ""))
    ]
    if active:
        raise TaskOwnershipError(
            agent_id=agent_id,
            task_id=task_id,
            action="claim",
            message=f"You already have an active task: {active[0].get('id')}. "
            "Complete or pause it before claiming new work.",
        )

    # Check team match (warning only - agents can claim cross-team if needed)
    agent_team = get_agent_team(agent_id)
    if agent_team and agent_team != task_team:
        # This is allowed but unusual - could log a warning
        pass

    return True


def can_review_task(
    agent_id: str,
    task_developed_by: str | None,
) -> bool:
    """
    Check if agent can review a task (QA).

    Rule: Cannot review your own work.

    Args:
        agent_id: The agent attempting to review
        task_developed_by: Who developed the task

    Returns:
        True if can review
    """
    if agent_id == task_developed_by:
        return False
    return True
