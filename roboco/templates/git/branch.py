"""
Branch Name Builder.

Generates git branch names following the format:
{type}/{team}/{root_short}--{sub_short}--{subsub_short}

Each `*_short` is the first 8 characters of the task UUID, per the CLAUDE.md
convention — full UUIDs produced 140-char branch names that were ugly and
fragile in some tools. 8-char prefix gives 16^8 = ~4 billion distinct values
per level, which is more than enough to distinguish siblings.

Uses '--' separator for task hierarchy to avoid git ref conflicts.
Git cannot have both 'foo' as a branch AND 'foo/bar' as another branch,
so we use '--' instead of '/' for the task hierarchy portion.

Max 3 levels deep (root → subtask → sub-subtask).
"""

from typing import TYPE_CHECKING
from uuid import UUID

from roboco.templates.git.constants import BRANCH_TYPES, MAX_TASK_DEPTH

# 8-char UUID prefix is the CLAUDE.md convention; 16**8 values per level is
# plenty of room for siblings while keeping branch names readable.
_SHORT_ID_LEN = 8

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
        Branch name in format: {type}/{team}/{root}--{sub}--{subsub}

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

        ancestors.append(str(task.id)[:_SHORT_ID_LEN])
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

    # Reverse to get root-first order: root--sub--subsub
    # Use '--' separator to avoid git ref conflicts
    ancestors.reverse()
    path = "--".join(ancestors)

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
