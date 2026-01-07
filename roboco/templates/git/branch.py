"""
Branch Name Builder.

Generates git branch names following the format:
{type}/{team}/{root_uuid}/{subtask_uuid}/{subsubtask_uuid}

Max 3 levels deep (root → subtask → sub-subtask).
"""

from typing import TYPE_CHECKING
from uuid import UUID

from roboco.templates.git.constants import BRANCH_TYPES, MAX_TASK_DEPTH

if TYPE_CHECKING:
    from roboco.services.task import TaskService


class BranchNameError(Exception):
    """Error building branch name."""


async def build_branch_name(
    task_id: UUID,
    branch_type: str,
    team: str,
    task_service: "TaskService",
) -> str:
    """Build branch name with ancestor path (max 3 levels, full UUIDs).

    Args:
        task_id: The task to create branch for
        branch_type: One of: feature, bug, chore, docs, hotfix
        team: Team identifier (backend, frontend, uxui)
        task_service: TaskService instance for fetching task hierarchy

    Returns:
        Branch name in format: {type}/{team}/{root}/{sub}/{subsub}

    Raises:
        BranchNameError: If branch_type invalid, task not found, or hierarchy too deep
    """
    # Validate branch type
    if branch_type not in BRANCH_TYPES:
        valid_types = ", ".join(sorted(BRANCH_TYPES))
        raise BranchNameError(
            f"Invalid branch type '{branch_type}'. Must be one of: {valid_types}"
        )

    # Walk up to root collecting task IDs
    ancestors: list[str] = []
    current_id: UUID | None = task_id
    depth = 0

    while current_id is not None and depth < MAX_TASK_DEPTH:
        task = await task_service.get(current_id)
        if task is None:
            raise BranchNameError(f"Task not found: {current_id}")

        ancestors.append(str(task.id))
        # parent_task_id is SQLAlchemy UUID type, cast to Python UUID
        parent_id = task.parent_task_id
        current_id = UUID(str(parent_id)) if parent_id is not None else None
        depth += 1

    # Check if hierarchy is too deep
    if current_id is not None:
        raise BranchNameError(
            f"Task hierarchy too deep (>{MAX_TASK_DEPTH} levels). "
            f"Task {task_id} has ancestors beyond the maximum depth."
        )

    # Reverse to get root-first order: root/sub/subsub
    ancestors.reverse()
    path = "/".join(ancestors)

    return f"{branch_type}/{team}/{path}"


async def get_root_task_id(
    task_id: UUID,
    task_service: "TaskService",
) -> UUID:
    """Get the root task ID by walking up the hierarchy.

    Args:
        task_id: Starting task ID
        task_service: TaskService instance

    Returns:
        The root task ID (task with no parent)

    Raises:
        BranchNameError: If task not found or hierarchy too deep
    """
    current_id: UUID | None = task_id
    root_id: UUID = task_id
    depth = 0

    while current_id is not None and depth < MAX_TASK_DEPTH:
        task = await task_service.get(current_id)
        if task is None:
            raise BranchNameError(f"Task not found: {current_id}")

        root_id = UUID(str(task.id))
        # parent_task_id is SQLAlchemy UUID type, cast to Python UUID
        parent_id = task.parent_task_id
        current_id = UUID(str(parent_id)) if parent_id is not None else None
        depth += 1

    if current_id is not None:
        raise BranchNameError(f"Task hierarchy too deep (>{MAX_TASK_DEPTH} levels)")

    return root_id
