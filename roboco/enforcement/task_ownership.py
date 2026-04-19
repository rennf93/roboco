"""
Task Ownership Enforcement

Validates task ownership and claim rules.
"""

from roboco.agents_config import get_agent_role, get_agent_team
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
    return agent_id != task_developed_by
