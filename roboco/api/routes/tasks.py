"""
Task API Routes

Full CRUD operations and lifecycle management for tasks.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from roboco.api.deps import (
    CurrentAgentContext,
    DbSession,
    PermissionServiceDep,
)
from roboco.models.base import Complexity, TaskStatus, Team
from roboco.services.audit import get_audit_service
from roboco.services.permissions import TaskAction
from roboco.services.task import TaskCreateRequest, get_task_service

router = APIRouter(prefix="/tasks", tags=["tasks"])


# =============================================================================
# REQUEST/RESPONSE MODELS
# =============================================================================


class TaskCreate(BaseModel):
    """Request to create a task."""

    title: str = Field(..., min_length=1, max_length=200)
    description: str
    acceptance_criteria: list[str] = Field(..., min_length=1)
    team: Team
    priority: int = Field(default=2, ge=0, le=3)
    parent_task_id: UUID | None = None
    target_date: datetime | None = None
    estimated_complexity: Complexity = Complexity.MEDIUM


class TaskUpdate(BaseModel):
    """Request to update a task."""

    title: str | None = None
    description: str | None = None
    acceptance_criteria: list[str] | None = None
    priority: int | None = Field(default=None, ge=0, le=3)
    target_date: datetime | None = None
    estimated_complexity: Complexity | None = None
    dev_notes: str | None = None
    quick_context: str | None = None


class TaskResponse(BaseModel):
    """Task response model."""

    id: UUID
    title: str
    description: str
    acceptance_criteria: list[str]
    status: TaskStatus
    priority: int
    team: Team
    created_by: UUID
    assigned_to: UUID | None
    parent_task_id: UUID | None
    dependency_ids: list[UUID]
    blocker_ids: list[UUID]
    created_at: datetime
    updated_at: datetime | None
    claimed_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    target_date: datetime | None
    estimated_complexity: Complexity
    self_verified: bool
    qa_verified: bool | None
    dev_notes: str | None
    qa_notes: str | None
    quick_context: str | None

    class Config:
        from_attributes = True


class ProgressRequest(BaseModel):
    """Request to add progress update."""

    message: str
    percentage: int | None = Field(default=None, ge=0, le=100)


class CheckpointRequest(BaseModel):
    """Request to add checkpoint."""

    state_summary: str
    remaining_work: list[str]
    notes: str | None = None


class CommitRequest(BaseModel):
    """Request to link a commit."""

    hash: str = Field(..., min_length=7, max_length=40)
    message: str


class QANotes(BaseModel):
    """QA review notes."""

    notes: str


class TaskCountResponse(BaseModel):
    """Task count by category."""

    counts: dict[str, int]


# =============================================================================
# QUERY PARAMETER MODELS
# =============================================================================


class ListTasksQuery(BaseModel):
    """Query params for listing tasks."""

    team: Team | None = None
    status: TaskStatus | None = None
    limit: int = Field(100, ge=1, le=500)
    offset: int = Field(0, ge=0)


class TeamTasksQuery(BaseModel):
    """Query params for team tasks."""

    task_status: TaskStatus | None = None
    limit: int = Field(100, ge=1, le=500)


# =============================================================================
# CRUD ENDPOINTS
# =============================================================================


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    data: TaskCreate,
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
):
    """Create a new task."""
    # Check create permission
    if not permissions.can_perform_task_action(agent, TaskAction.CREATE, data.team):
        # Log the denial
        audit = get_audit_service()
        await audit.log_task_action_denial(
            agent_id=agent.agent_id,
            agent_role=agent.role.value,
            task_id="N/A",
            action="create",
            reason="Role not permitted to create tasks",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to create tasks",
        )

    service = get_task_service(db)
    req = TaskCreateRequest(
        title=data.title,
        description=data.description,
        acceptance_criteria=data.acceptance_criteria,
        team=data.team,
        created_by=agent.agent_id,
        priority=data.priority,
        parent_task_id=data.parent_task_id,
        target_date=data.target_date,
        estimated_complexity=data.estimated_complexity,
    )
    task = await service.create(req)
    await db.commit()
    return task


@router.get("", response_model=list[TaskResponse])
async def list_tasks(
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
    params: Annotated[ListTasksQuery, Query()],
):
    """
    List tasks with optional filters.

    View permissions:
    - Main PM, Board, Auditor: Can see all tasks
    - Cell PM: Can see own cell's tasks
    - Cell members: Can only see own cell's tasks
    """
    service = get_task_service(db)

    # Determine effective team filter based on permissions
    can_view_all = permissions.can_perform_task_action(agent, TaskAction.VIEW_ALL)
    effective_team = params.team

    if not can_view_all:
        # Cell members can only see their own team's tasks
        if agent.team:
            effective_team = agent.team
        else:
            # No team assigned - return empty list
            return []

    if effective_team and params.status:
        tasks = await service.list_by_team(effective_team, params.status, params.limit)
    elif effective_team:
        tasks = await service.list_by_team(effective_team, limit=params.limit)
    elif params.status:
        tasks = await service.list_by_status(params.status)
    else:
        tasks = await service.list_all(params.limit, params.offset)

    return tasks


@router.get("/my", response_model=list[TaskResponse])
async def get_my_tasks(
    db: DbSession,
    agent: CurrentAgentContext,
    status: TaskStatus | None = None,
):
    """Get tasks assigned to the current agent."""
    service = get_task_service(db)
    return await service.list_by_assignee(agent.agent_id, status)


@router.get("/pending", response_model=list[TaskResponse])
async def get_pending_tasks(
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
    team: Team | None = None,
):
    """Get pending tasks available to claim."""
    service = get_task_service(db)

    # Apply team filter based on permissions
    can_view_all = permissions.can_perform_task_action(agent, TaskAction.VIEW_ALL)
    effective_team = team if can_view_all else agent.team

    return await service.list_pending(effective_team)


@router.get("/blocked", response_model=list[TaskResponse])
async def get_blocked_tasks(
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
    team: Team | None = None,
):
    """Get blocked tasks."""
    service = get_task_service(db)

    # Apply team filter based on permissions
    can_view_all = permissions.can_perform_task_action(agent, TaskAction.VIEW_ALL)
    effective_team = team if can_view_all else agent.team

    return await service.list_blocked(effective_team)


@router.get("/awaiting-qa", response_model=list[TaskResponse])
async def get_awaiting_qa_tasks(
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
    team: Team | None = None,
):
    """Get tasks awaiting QA review."""
    service = get_task_service(db)

    # Apply team filter based on permissions
    can_view_all = permissions.can_perform_task_action(agent, TaskAction.VIEW_ALL)
    effective_team = team if can_view_all else agent.team

    return await service.list_awaiting_qa(effective_team)


@router.get("/awaiting-docs", response_model=list[TaskResponse])
async def get_awaiting_docs_tasks(
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
    team: Team | None = None,
):
    """Get tasks awaiting documentation."""
    service = get_task_service(db)

    # Apply team filter based on permissions
    can_view_all = permissions.can_perform_task_action(agent, TaskAction.VIEW_ALL)
    effective_team = team if can_view_all else agent.team

    return await service.list_awaiting_docs(effective_team)


@router.get("/team/{team}", response_model=list[TaskResponse])
async def get_team_tasks(
    team: Team,
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
    params: Annotated[TeamTasksQuery, Query()],
):
    """Get tasks for a specific team."""
    # Check if agent can view this team's tasks
    can_view_all = permissions.can_perform_task_action(agent, TaskAction.VIEW_ALL)
    is_own_team = agent.team == team

    if not can_view_all and not is_own_team:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this team's tasks",
        )

    service = get_task_service(db)
    return await service.list_by_team(team, params.task_status, params.limit)


@router.get("/stats", response_model=TaskCountResponse)
async def get_task_stats(
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
    team: Team | None = None,
):
    """Get task counts by status."""
    service = get_task_service(db)

    # Apply team filter based on permissions
    can_view_all = permissions.can_perform_task_action(agent, TaskAction.VIEW_ALL)
    effective_team = team if can_view_all else agent.team

    counts = await service.count_by_status(effective_team)
    return TaskCountResponse(counts=counts)


@router.get("/stats/by-team", response_model=TaskCountResponse)
async def get_task_stats_by_team(
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
):
    """Get task counts by team."""
    # Only agents with VIEW_ALL can see cross-team stats
    can_view_all = permissions.can_perform_task_action(agent, TaskAction.VIEW_ALL)
    if not can_view_all:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view cross-team statistics",
        )

    service = get_task_service(db)
    counts = await service.count_by_team()
    return TaskCountResponse(counts=counts)


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: UUID,
    db: DbSession,
):
    """Get a specific task."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.put("/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: UUID,
    data: TaskUpdate,
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
):
    """Update a task."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Check if agent can update this task
    # UPDATE_OWN requires agent to be assigned to or created the task
    is_owner = agent.agent_id in {task.assigned_to, task.created_by}
    can_update_own = permissions.can_perform_task_action(
        agent, TaskAction.UPDATE_OWN, task.team
    )
    has_higher_perms = permissions.can_perform_task_action(
        agent, TaskAction.ASSIGN, task.team
    )

    if not ((can_update_own and is_owner) or has_higher_perms):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this task",
        )

    task = await service.update(task_id, **data.model_dump(exclude_unset=True))
    await db.commit()
    return task


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
):
    """Delete a task."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Only creators or agents with ASSIGN permission can delete tasks
    is_creator = task.created_by == agent.agent_id
    has_assign_perms = permissions.can_perform_task_action(
        agent, TaskAction.ASSIGN, task.team
    )

    if not (is_creator or has_assign_perms):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to delete this task",
        )

    await service.delete(task_id)
    await db.commit()


@router.get("/{task_id}/subtasks", response_model=list[TaskResponse])
async def get_subtasks(
    task_id: UUID,
    db: DbSession,
):
    """Get subtasks of a task."""
    service = get_task_service(db)
    return await service.get_subtasks(task_id)


# =============================================================================
# LIFECYCLE ENDPOINTS
# =============================================================================


@router.post("/{task_id}/claim", response_model=TaskResponse)
async def claim_task(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
):
    """Claim a task."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Check claim permission
    if not permissions.can_perform_task_action(agent, TaskAction.CLAIM, task.team):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to claim tasks",
        )

    task = await service.claim(task_id, agent.agent_id)
    if not task:
        raise HTTPException(
            status_code=400,
            detail="Cannot claim task - not pending",
        )
    await db.commit()
    return task


@router.post("/{task_id}/start", response_model=TaskResponse)
async def start_task(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
):
    """Start working on a task."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Only assigned agent can start the task
    if task.assigned_to != agent.agent_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the assigned agent can start this task",
        )

    task = await service.start(task_id)
    if not task:
        raise HTTPException(
            status_code=400,
            detail="Cannot start task - invalid status",
        )
    await db.commit()
    return task


@router.post("/{task_id}/block", response_model=TaskResponse)
async def block_task(
    task_id: UUID,
    blocker_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
):
    """Block a task due to a dependency."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Only assigned agent or PM can block a task
    if task.assigned_to != agent.agent_id and agent.role.value not in (
        "cell_pm",
        "main_pm",
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to block this task",
        )

    task = await service.block(task_id, blocker_id)
    await db.commit()
    return task


@router.post("/{task_id}/unblock", response_model=TaskResponse)
async def unblock_task(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
):
    """Unblock a task."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Only assigned agent or PM can unblock a task
    if task.assigned_to != agent.agent_id and agent.role.value not in (
        "cell_pm",
        "main_pm",
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to unblock this task",
        )

    task = await service.unblock(task_id)
    if not task:
        raise HTTPException(
            status_code=400,
            detail="Cannot unblock task - not blocked",
        )
    await db.commit()
    return task


@router.post("/{task_id}/pause", response_model=TaskResponse)
async def pause_task(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
):
    """Pause a task."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Only assigned agent can pause their task
    if task.assigned_to != agent.agent_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the assigned agent can pause this task",
        )

    task = await service.pause(task_id)
    if not task:
        raise HTTPException(
            status_code=400,
            detail="Cannot pause task - not in progress",
        )
    await db.commit()
    return task


@router.post("/{task_id}/resume", response_model=TaskResponse)
async def resume_task(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
):
    """Resume a paused task."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Only assigned agent can resume their task
    if task.assigned_to != agent.agent_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the assigned agent can resume this task",
        )

    task = await service.resume(task_id)
    if not task:
        raise HTTPException(
            status_code=400,
            detail="Cannot resume task - not paused",
        )
    await db.commit()
    return task


@router.post("/{task_id}/verify", response_model=TaskResponse)
async def submit_for_verification(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
):
    """Submit task for self-verification."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Only assigned agent can submit for verification
    if task.assigned_to != agent.agent_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the assigned agent can submit for verification",
        )

    task = await service.submit_for_verification(task_id)
    if not task:
        raise HTTPException(
            status_code=400,
            detail="Cannot verify task - not in progress",
        )
    await db.commit()
    return task


@router.post("/{task_id}/submit-qa", response_model=TaskResponse)
async def submit_for_qa(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
):
    """Submit task for QA review."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Only assigned agent can submit for QA
    if task.assigned_to != agent.agent_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the assigned agent can submit for QA",
        )

    task = await service.submit_for_qa(task_id)
    if not task:
        raise HTTPException(
            status_code=400,
            detail="Cannot submit for QA - not verifying",
        )
    await db.commit()
    return task


@router.post("/{task_id}/pass-qa", response_model=TaskResponse)
async def pass_qa(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
    data: QANotes | None = None,
):
    """Mark task as passed QA."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Only QA agents can pass/fail QA
    if agent.role.value != "qa":
        audit = get_audit_service()
        await audit.log_task_action_denial(
            agent_id=agent.agent_id,
            agent_role=agent.role.value,
            task_id=task_id,
            action="pass_qa",
            reason="Only QA agents can pass QA reviews",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only QA agents can pass QA reviews",
        )

    # QA cannot review their own tasks (prevent self-review)
    if task.assigned_to == agent.agent_id:
        audit = get_audit_service()
        await audit.log_task_action_denial(
            agent_id=agent.agent_id,
            agent_role=agent.role.value,
            task_id=task_id,
            action="pass_qa",
            reason="Self-review not permitted",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot QA review your own task",
        )

    notes = data.notes if data else None
    task = await service.pass_qa(task_id, notes)
    if not task:
        raise HTTPException(
            status_code=400,
            detail="Cannot pass QA - not awaiting QA",
        )
    await db.commit()
    return task


@router.post("/{task_id}/fail-qa", response_model=TaskResponse)
async def fail_qa(
    task_id: UUID,
    data: QANotes,
    db: DbSession,
    agent: CurrentAgentContext,
):
    """Mark task as failed QA."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Only QA agents can pass/fail QA
    if agent.role.value != "qa":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only QA agents can fail QA reviews",
        )

    # QA cannot review their own tasks (prevent self-review)
    if task.assigned_to == agent.agent_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot QA review your own task",
        )

    task = await service.fail_qa(task_id, data.notes)
    if not task:
        raise HTTPException(
            status_code=400,
            detail="Cannot fail QA - not awaiting QA",
        )
    await db.commit()
    return task


@router.post("/{task_id}/complete", response_model=TaskResponse)
async def complete_task(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
):
    """Mark task as completed."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Check close permission - assigned agent or those with CLOSE permission
    is_assigned = task.assigned_to == agent.agent_id
    can_close = permissions.can_perform_task_action(agent, TaskAction.CLOSE, task.team)

    if not (is_assigned or can_close):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to complete this task",
        )

    task = await service.complete(task_id)
    if not task:
        raise HTTPException(
            status_code=400,
            detail="Cannot complete task - invalid status",
        )
    await db.commit()
    return task


@router.post("/{task_id}/cancel", response_model=TaskResponse)
async def cancel_task(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
):
    """Cancel a task."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Only PM or higher can cancel tasks
    can_cancel = permissions.can_perform_task_action(
        agent, TaskAction.CHANGE_PRIORITY, task.team
    )
    if not can_cancel:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to cancel tasks",
        )

    task = await service.cancel(task_id)
    await db.commit()
    return task


# =============================================================================
# PROGRESS AND ARTIFACTS
# =============================================================================


@router.post("/{task_id}/progress", response_model=TaskResponse)
async def add_progress(
    task_id: UUID,
    data: ProgressRequest,
    db: DbSession,
    agent: CurrentAgentContext,
):
    """Add a progress update to a task."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Only assigned agent can add progress
    if task.assigned_to != agent.agent_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the assigned agent can add progress updates",
        )

    task = await service.add_progress(
        task_id, agent.agent_id, data.message, data.percentage
    )
    await db.commit()
    return task


@router.post("/{task_id}/checkpoint", response_model=TaskResponse)
async def add_checkpoint(
    task_id: UUID,
    data: CheckpointRequest,
    db: DbSession,
    agent: CurrentAgentContext,
):
    """Add a checkpoint for state recovery."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Only assigned agent can add checkpoints
    if task.assigned_to != agent.agent_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the assigned agent can add checkpoints",
        )

    task = await service.add_checkpoint(
        task_id,
        agent.agent_id,
        data.state_summary,
        data.remaining_work,
        data.notes,
    )
    await db.commit()
    return task


@router.post("/{task_id}/commit", response_model=TaskResponse)
async def add_commit(
    task_id: UUID,
    data: CommitRequest,
    db: DbSession,
    agent: CurrentAgentContext,
):
    """Link a commit to a task."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Only assigned agent can link commits
    if task.assigned_to != agent.agent_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the assigned agent can link commits",
        )

    task = await service.add_commit(task_id, data.hash, data.message, agent.agent_id)
    await db.commit()
    return task
