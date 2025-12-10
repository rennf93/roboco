"""
Task API Routes

Full CRUD operations and lifecycle management for tasks.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.api.deps import get_current_agent_id, get_db
from roboco.models.base import Complexity, TaskStatus, Team
from roboco.services.task import get_task_service

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
# CRUD ENDPOINTS
# =============================================================================


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    data: TaskCreate,
    db: Annotated[AsyncSession, Depends(get_db)],
    agent_id: Annotated[UUID, Depends(get_current_agent_id)],
):
    """Create a new task."""
    service = get_task_service(db)
    task = await service.create(
        title=data.title,
        description=data.description,
        acceptance_criteria=data.acceptance_criteria,
        team=data.team,
        created_by=agent_id,
        priority=data.priority,
        parent_task_id=data.parent_task_id,
        target_date=data.target_date,
        estimated_complexity=data.estimated_complexity,
    )
    await db.commit()
    return task


@router.get("", response_model=list[TaskResponse])
async def list_tasks(
    db: Annotated[AsyncSession, Depends(get_db)],
    team: Team | None = None,
    status: TaskStatus | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    """List tasks with optional filters."""
    service = get_task_service(db)

    if team and status:
        tasks = await service.list_by_team(team, status, limit)
    elif team:
        tasks = await service.list_by_team(team, limit=limit)
    elif status:
        tasks = await service.list_by_status(status)
    else:
        tasks = await service.list_all(limit, offset)

    return tasks


@router.get("/my", response_model=list[TaskResponse])
async def get_my_tasks(
    db: Annotated[AsyncSession, Depends(get_db)],
    agent_id: Annotated[UUID, Depends(get_current_agent_id)],
    status: TaskStatus | None = None,
):
    """Get tasks assigned to the current agent."""
    service = get_task_service(db)
    return await service.list_by_assignee(agent_id, status)


@router.get("/pending", response_model=list[TaskResponse])
async def get_pending_tasks(
    db: Annotated[AsyncSession, Depends(get_db)],
    team: Team | None = None,
):
    """Get pending tasks available to claim."""
    service = get_task_service(db)
    return await service.list_pending(team)


@router.get("/blocked", response_model=list[TaskResponse])
async def get_blocked_tasks(
    db: Annotated[AsyncSession, Depends(get_db)],
    team: Team | None = None,
):
    """Get blocked tasks."""
    service = get_task_service(db)
    return await service.list_blocked(team)


@router.get("/awaiting-qa", response_model=list[TaskResponse])
async def get_awaiting_qa_tasks(
    db: Annotated[AsyncSession, Depends(get_db)],
    team: Team | None = None,
):
    """Get tasks awaiting QA review."""
    service = get_task_service(db)
    return await service.list_awaiting_qa(team)


@router.get("/awaiting-docs", response_model=list[TaskResponse])
async def get_awaiting_docs_tasks(
    db: Annotated[AsyncSession, Depends(get_db)],
    team: Team | None = None,
):
    """Get tasks awaiting documentation."""
    service = get_task_service(db)
    return await service.list_awaiting_docs(team)


@router.get("/team/{team}", response_model=list[TaskResponse])
async def get_team_tasks(
    team: Team,
    db: Annotated[AsyncSession, Depends(get_db)],
    status: TaskStatus | None = None,
    limit: int = Query(default=100, ge=1, le=500),
):
    """Get tasks for a specific team."""
    service = get_task_service(db)
    return await service.list_by_team(team, status, limit)


@router.get("/stats", response_model=TaskCountResponse)
async def get_task_stats(
    db: Annotated[AsyncSession, Depends(get_db)],
    team: Team | None = None,
):
    """Get task counts by status."""
    service = get_task_service(db)
    counts = await service.count_by_status(team)
    return TaskCountResponse(counts=counts)


@router.get("/stats/by-team", response_model=TaskCountResponse)
async def get_task_stats_by_team(
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Get task counts by team."""
    service = get_task_service(db)
    counts = await service.count_by_team()
    return TaskCountResponse(counts=counts)


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
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
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Update a task."""
    service = get_task_service(db)
    task = await service.update(task_id, **data.model_dump(exclude_unset=True))
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    await db.commit()
    return task


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Delete a task."""
    service = get_task_service(db)
    deleted = await service.delete(task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Task not found")
    await db.commit()


@router.get("/{task_id}/subtasks", response_model=list[TaskResponse])
async def get_subtasks(
    task_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
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
    db: Annotated[AsyncSession, Depends(get_db)],
    agent_id: Annotated[UUID, Depends(get_current_agent_id)],
):
    """Claim a task."""
    service = get_task_service(db)
    task = await service.claim(task_id, agent_id)
    if not task:
        raise HTTPException(
            status_code=400,
            detail="Cannot claim task - not found or not pending",
        )
    await db.commit()
    return task


@router.post("/{task_id}/start", response_model=TaskResponse)
async def start_task(
    task_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Start working on a task."""
    service = get_task_service(db)
    task = await service.start(task_id)
    if not task:
        raise HTTPException(
            status_code=400,
            detail="Cannot start task - not found or invalid status",
        )
    await db.commit()
    return task


@router.post("/{task_id}/block", response_model=TaskResponse)
async def block_task(
    task_id: UUID,
    blocker_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Block a task due to a dependency."""
    service = get_task_service(db)
    task = await service.block(task_id, blocker_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    await db.commit()
    return task


@router.post("/{task_id}/unblock", response_model=TaskResponse)
async def unblock_task(
    task_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Unblock a task."""
    service = get_task_service(db)
    task = await service.unblock(task_id)
    if not task:
        raise HTTPException(
            status_code=400,
            detail="Cannot unblock task - not found or not blocked",
        )
    await db.commit()
    return task


@router.post("/{task_id}/pause", response_model=TaskResponse)
async def pause_task(
    task_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Pause a task."""
    service = get_task_service(db)
    task = await service.pause(task_id)
    if not task:
        raise HTTPException(
            status_code=400,
            detail="Cannot pause task - not found or not in progress",
        )
    await db.commit()
    return task


@router.post("/{task_id}/resume", response_model=TaskResponse)
async def resume_task(
    task_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Resume a paused task."""
    service = get_task_service(db)
    task = await service.resume(task_id)
    if not task:
        raise HTTPException(
            status_code=400,
            detail="Cannot resume task - not found or not paused",
        )
    await db.commit()
    return task


@router.post("/{task_id}/verify", response_model=TaskResponse)
async def submit_for_verification(
    task_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Submit task for self-verification."""
    service = get_task_service(db)
    task = await service.submit_for_verification(task_id)
    if not task:
        raise HTTPException(
            status_code=400,
            detail="Cannot verify task - not found or not in progress",
        )
    await db.commit()
    return task


@router.post("/{task_id}/submit-qa", response_model=TaskResponse)
async def submit_for_qa(
    task_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Submit task for QA review."""
    service = get_task_service(db)
    task = await service.submit_for_qa(task_id)
    if not task:
        raise HTTPException(
            status_code=400,
            detail="Cannot submit for QA - not found or not verifying",
        )
    await db.commit()
    return task


@router.post("/{task_id}/pass-qa", response_model=TaskResponse)
async def pass_qa(
    task_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
    data: QANotes | None = None,
):
    """Mark task as passed QA."""
    service = get_task_service(db)
    notes = data.notes if data else None
    task = await service.pass_qa(task_id, notes)
    if not task:
        raise HTTPException(
            status_code=400,
            detail="Cannot pass QA - not found or not awaiting QA",
        )
    await db.commit()
    return task


@router.post("/{task_id}/fail-qa", response_model=TaskResponse)
async def fail_qa(
    task_id: UUID,
    data: QANotes,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Mark task as failed QA."""
    service = get_task_service(db)
    task = await service.fail_qa(task_id, data.notes)
    if not task:
        raise HTTPException(
            status_code=400,
            detail="Cannot fail QA - not found or not awaiting QA",
        )
    await db.commit()
    return task


@router.post("/{task_id}/complete", response_model=TaskResponse)
async def complete_task(
    task_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Mark task as completed."""
    service = get_task_service(db)
    task = await service.complete(task_id)
    if not task:
        raise HTTPException(
            status_code=400,
            detail="Cannot complete task - not found or invalid status",
        )
    await db.commit()
    return task


@router.post("/{task_id}/cancel", response_model=TaskResponse)
async def cancel_task(
    task_id: UUID,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Cancel a task."""
    service = get_task_service(db)
    task = await service.cancel(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    await db.commit()
    return task


# =============================================================================
# PROGRESS AND ARTIFACTS
# =============================================================================


@router.post("/{task_id}/progress", response_model=TaskResponse)
async def add_progress(
    task_id: UUID,
    data: ProgressRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    agent_id: Annotated[UUID, Depends(get_current_agent_id)],
):
    """Add a progress update to a task."""
    service = get_task_service(db)
    task = await service.add_progress(task_id, agent_id, data.message, data.percentage)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    await db.commit()
    return task


@router.post("/{task_id}/checkpoint", response_model=TaskResponse)
async def add_checkpoint(
    task_id: UUID,
    data: CheckpointRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    agent_id: Annotated[UUID, Depends(get_current_agent_id)],
):
    """Add a checkpoint for state recovery."""
    service = get_task_service(db)
    task = await service.add_checkpoint(
        task_id,
        agent_id,
        data.state_summary,
        data.remaining_work,
        data.notes,
    )
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    await db.commit()
    return task


@router.post("/{task_id}/commit", response_model=TaskResponse)
async def add_commit(
    task_id: UUID,
    data: CommitRequest,
    db: Annotated[AsyncSession, Depends(get_db)],
    agent_id: Annotated[UUID, Depends(get_current_agent_id)],
):
    """Link a commit to a task."""
    service = get_task_service(db)
    task = await service.add_commit(task_id, data.hash, data.message, agent_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    await db.commit()
    return task
