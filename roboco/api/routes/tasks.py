"""
Task API Routes

Full CRUD operations and lifecycle management for tasks.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Query, status
from sqlalchemy import select

from roboco.api.deps import (
    CurrentAgentContext,
    DbSession,
    PermissionServiceDep,
)
from roboco.api.schemas.tasks import (
    CheckpointRequest,
    CheckpointResponse,
    ClaimRequest,
    CommitRefResponse,
    CommitRequest,
    ListTasksQuery,
    ProgressRequest,
    ProgressUpdateResponse,
    QANotes,
    SubTaskResponse,
    TaskCountResponse,
    TaskPlanResponse,
    TaskResponse,
    TaskUpdate,
    TeamTasksQuery,
)
from roboco.db.tables import AgentTable, TaskTable
from roboco.models.base import TaskStatus, Team
from roboco.models.task import TaskCreate
from roboco.services.audit import get_audit_service
from roboco.services.permissions import TaskAction
from roboco.services.task import TaskCreateRequest, get_task_service
from roboco.utils.converters import require_uuid, to_python_uuid, to_python_uuid_list

router = APIRouter()


def _convert_plan(plan_data: dict | None) -> TaskPlanResponse | None:
    """Convert plan JSON dict to TaskPlanResponse."""
    if not plan_data:
        return None

    sub_tasks = []
    for st in plan_data.get("sub_tasks", []):
        sub_tasks.append(
            SubTaskResponse(
                id=st.get("id"),
                title=st.get("title", ""),
                description=st.get("description"),
                completed=st.get("completed", False),
                order=st.get("order", 0),
                estimated_hours=st.get("estimated_hours"),
                notes=st.get("notes"),
            )
        )

    return TaskPlanResponse(
        approach=plan_data.get("approach", ""),
        sub_tasks=sub_tasks,
        technical_considerations=plan_data.get("technical_considerations", []),
        risks=plan_data.get("risks", []),
        open_questions=plan_data.get("open_questions", []),
    )


def _convert_checkpoints(checkpoints_data: list | None) -> list[CheckpointResponse]:
    """Convert checkpoints JSON list to CheckpointResponse list."""
    if not checkpoints_data:
        return []

    result = []
    for cp in checkpoints_data:
        result.append(
            CheckpointResponse(
                id=cp.get("id"),
                timestamp=cp.get("timestamp"),
                agent_id=cp.get("agent_id"),
                state_summary=cp.get("state_summary", ""),
                remaining_work=cp.get("remaining_work", []),
                notes=cp.get("notes"),
            )
        )
    return result


def _convert_progress_updates(
    updates_data: list | None,
) -> list[ProgressUpdateResponse]:
    """Convert progress_updates JSON list to ProgressUpdateResponse list."""
    if not updates_data:
        return []

    result = []
    for pu in updates_data:
        result.append(
            ProgressUpdateResponse(
                timestamp=pu.get("timestamp"),
                agent_id=pu.get("agent_id"),
                message=pu.get("message", ""),
                percentage=pu.get("percentage"),
            )
        )
    return result


def _convert_commits(commits_data: list | None) -> list[CommitRefResponse]:
    """Convert commits JSON list to CommitRefResponse list."""
    if not commits_data:
        return []

    result = []
    for cm in commits_data:
        result.append(
            CommitRefResponse(
                hash=cm.get("hash", ""),
                message=cm.get("message", ""),
                timestamp=cm.get("timestamp"),
                author_agent_id=cm.get("author_agent_id"),
            )
        )
    return result


def _to_response(task: TaskTable) -> TaskResponse:
    """Convert TaskTable to TaskResponse with proper UUID conversion."""
    return TaskResponse(
        id=require_uuid(task.id),
        title=task.title,
        description=task.description,
        acceptance_criteria=task.acceptance_criteria or [],
        status=task.status,
        priority=task.priority,
        team=task.team,
        created_by=require_uuid(task.created_by),
        assigned_to=to_python_uuid(task.assigned_to),
        parent_task_id=to_python_uuid(task.parent_task_id),
        dependency_ids=to_python_uuid_list(task.dependency_ids),
        blocker_ids=to_python_uuid_list(task.blocker_ids),
        created_at=task.created_at,
        updated_at=task.updated_at,
        claimed_at=task.claimed_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        target_date=task.target_date,
        estimated_complexity=task.estimated_complexity,
        # Planning
        plan=_convert_plan(task.plan),
        # Execution
        checkpoints=_convert_checkpoints(task.checkpoints),
        progress_updates=_convert_progress_updates(task.progress_updates),
        # Artifacts
        commits=_convert_commits(task.commits),
        # Documentation
        dev_notes=task.dev_notes,
        qa_notes=task.qa_notes,
        auditor_notes=task.auditor_notes,
        quick_context=task.quick_context,
        # Review Status
        self_verified=task.self_verified,
        qa_verified=task.qa_verified,
    )


def _to_response_list(tasks: list[TaskTable]) -> list[TaskResponse]:
    """Convert list of TaskTable to list of TaskResponse."""
    return [_to_response(t) for t in tasks]


# =============================================================================
# CRUD ENDPOINTS
# =============================================================================


@router.post("", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
async def create_task(
    data: TaskCreate,
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
) -> TaskResponse:
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
    return _to_response(task)


@router.get("", response_model=list[TaskResponse])
async def list_tasks(
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
    params: Annotated[ListTasksQuery, Query()],
) -> list[TaskResponse]:
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

    return _to_response_list(tasks)


@router.get("/my", response_model=list[TaskResponse])
async def get_my_tasks(
    db: DbSession,
    agent: CurrentAgentContext,
    status: TaskStatus | None = None,
) -> list[TaskResponse]:
    """Get tasks assigned to the current agent."""
    service = get_task_service(db)
    tasks = await service.list_by_assignee(agent.agent_id, status)
    return _to_response_list(tasks)


@router.get("/pending", response_model=list[TaskResponse])
async def get_pending_tasks(
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
    team: Team | None = None,
) -> list[TaskResponse]:
    """Get pending tasks available to claim."""
    service = get_task_service(db)

    # Apply team filter based on permissions
    can_view_all = permissions.can_perform_task_action(agent, TaskAction.VIEW_ALL)
    effective_team = team if can_view_all else agent.team

    tasks = await service.list_pending(effective_team)
    return _to_response_list(tasks)


@router.get("/blocked", response_model=list[TaskResponse])
async def get_blocked_tasks(
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
    team: Team | None = None,
) -> list[TaskResponse]:
    """Get blocked tasks."""
    service = get_task_service(db)

    # Apply team filter based on permissions
    can_view_all = permissions.can_perform_task_action(agent, TaskAction.VIEW_ALL)
    effective_team = team if can_view_all else agent.team

    tasks = await service.list_blocked(effective_team)
    return _to_response_list(tasks)


@router.get("/awaiting-qa", response_model=list[TaskResponse])
async def get_awaiting_qa_tasks(
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
    team: Team | None = None,
) -> list[TaskResponse]:
    """Get tasks awaiting QA review."""
    service = get_task_service(db)

    # Apply team filter based on permissions
    can_view_all = permissions.can_perform_task_action(agent, TaskAction.VIEW_ALL)
    effective_team = team if can_view_all else agent.team

    tasks = await service.list_awaiting_qa(effective_team)
    return _to_response_list(tasks)


@router.get("/awaiting-docs", response_model=list[TaskResponse])
async def get_awaiting_docs_tasks(
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
    team: Team | None = None,
) -> list[TaskResponse]:
    """Get tasks awaiting documentation."""
    service = get_task_service(db)

    # Apply team filter based on permissions
    can_view_all = permissions.can_perform_task_action(agent, TaskAction.VIEW_ALL)
    effective_team = team if can_view_all else agent.team

    tasks = await service.list_awaiting_docs(effective_team)
    return _to_response_list(tasks)


@router.get("/team/{team}", response_model=list[TaskResponse])
async def get_team_tasks(
    team: Team,
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
    params: Annotated[TeamTasksQuery, Query()],
) -> list[TaskResponse]:
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
    tasks = await service.list_by_team(team, params.task_status, params.limit)
    return _to_response_list(tasks)


@router.get("/stats", response_model=TaskCountResponse)
async def get_task_stats(
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
    team: Team | None = None,
) -> TaskCountResponse:
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
) -> TaskCountResponse:
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
) -> TaskResponse:
    """Get a specific task."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )
    return _to_response(task)


@router.put("/{task_id}", response_model=TaskResponse)
@router.patch("/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: UUID,
    data: TaskUpdate,
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
) -> TaskResponse:
    """Update a task. Supports both PUT and PATCH for partial updates."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

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
    if not task:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Task update failed unexpectedly",
        )
    await db.commit()
    return _to_response(task)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
) -> None:
    """Delete a task."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

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
) -> list[TaskResponse]:
    """Get subtasks of a task."""
    service = get_task_service(db)
    tasks = await service.get_subtasks(task_id)
    return _to_response_list(tasks)


# =============================================================================
# LIFECYCLE ENDPOINTS
# =============================================================================


@router.post("/{task_id}/claim", response_model=TaskResponse)
async def claim_task(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
    data: Annotated[ClaimRequest | None, Body()] = None,
) -> TaskResponse:
    """
    Claim a task.

    Privileged roles (system, PM) can claim tasks on behalf of other agents
    by providing agent_id in the request body.
    """
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

    # Check claim permission
    if not permissions.can_perform_task_action(agent, TaskAction.CLAIM, task.team):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to claim tasks",
        )

    # Determine the agent to claim for
    # Privileged roles can claim on behalf of other agents
    can_assign = permissions.can_perform_task_action(
        agent, TaskAction.ASSIGN, task.team
    )
    if data and data.agent_id and can_assign:
        # Resolve agent_id from UUID string or slug
        agent_id_str = data.agent_id
        try:
            # Try parsing as UUID first
            claim_agent_id = UUID(agent_id_str)
        except ValueError:
            # Not a UUID, look up by slug
            result = await db.execute(
                select(AgentTable.id).where(AgentTable.slug == agent_id_str)
            )
            agent_uuid = result.scalar_one_or_none()
            if not agent_uuid:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Agent not found: {agent_id_str}",
                ) from None
            claim_agent_id = agent_uuid
    else:
        claim_agent_id = agent.agent_id

    task = await service.claim(task_id, claim_agent_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot claim task - not pending",
        )
    await db.commit()
    return _to_response(task)


@router.post("/{task_id}/start", response_model=TaskResponse)
async def start_task(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
) -> TaskResponse:
    """Start working on a task."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

    # Only assigned agent can start the task
    if task.assigned_to != agent.agent_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the assigned agent can start this task",
        )

    task = await service.start(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot start task - invalid status",
        )
    await db.commit()
    return _to_response(task)


@router.post("/{task_id}/block", response_model=TaskResponse)
async def block_task(
    task_id: UUID,
    blocker_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
) -> TaskResponse:
    """Block a task due to a dependency."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

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
    if not task:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Task block failed unexpectedly",
        )
    await db.commit()
    return _to_response(task)


@router.post("/{task_id}/unblock", response_model=TaskResponse)
async def unblock_task(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
) -> TaskResponse:
    """Unblock a task."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

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
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot unblock task - not blocked",
        )
    await db.commit()
    return _to_response(task)


@router.post("/{task_id}/pause", response_model=TaskResponse)
async def pause_task(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
) -> TaskResponse:
    """Pause a task."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

    # Only assigned agent can pause their task
    if task.assigned_to != agent.agent_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the assigned agent can pause this task",
        )

    task = await service.pause(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot pause task - not in progress",
        )
    await db.commit()
    return _to_response(task)


@router.post("/{task_id}/resume", response_model=TaskResponse)
async def resume_task(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
) -> TaskResponse:
    """Resume a paused task."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

    # Only assigned agent can resume their task
    if task.assigned_to != agent.agent_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the assigned agent can resume this task",
        )

    task = await service.resume(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot resume task - not paused",
        )
    await db.commit()
    return _to_response(task)


@router.post("/{task_id}/verify", response_model=TaskResponse)
async def submit_for_verification(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
) -> TaskResponse:
    """Submit task for self-verification."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

    # Only assigned agent can submit for verification
    if task.assigned_to != agent.agent_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the assigned agent can submit for verification",
        )

    task = await service.submit_for_verification(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot verify task - not in progress",
        )
    await db.commit()
    return _to_response(task)


@router.post("/{task_id}/submit-qa", response_model=TaskResponse)
async def submit_for_qa(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
) -> TaskResponse:
    """Submit task for QA review."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

    # Only assigned agent can submit for QA
    if task.assigned_to != agent.agent_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the assigned agent can submit for QA",
        )

    task = await service.submit_for_qa(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot submit for QA - not verifying",
        )
    await db.commit()
    return _to_response(task)


@router.post("/{task_id}/pass-qa", response_model=TaskResponse)
async def pass_qa(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
    data: QANotes | None = None,
) -> TaskResponse:
    """Mark task as passed QA."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

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
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot pass QA - not awaiting QA",
        )
    await db.commit()
    return _to_response(task)


@router.post("/{task_id}/fail-qa", response_model=TaskResponse)
async def fail_qa(
    task_id: UUID,
    data: QANotes,
    db: DbSession,
    agent: CurrentAgentContext,
) -> TaskResponse:
    """Mark task as failed QA."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

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
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot fail QA - not awaiting QA",
        )
    await db.commit()
    return _to_response(task)


@router.post("/{task_id}/complete", response_model=TaskResponse)
async def complete_task(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
) -> TaskResponse:
    """Mark task as completed."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

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
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot complete task - invalid status",
        )
    await db.commit()
    return _to_response(task)


@router.post("/{task_id}/cancel", response_model=TaskResponse)
async def cancel_task(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
) -> TaskResponse:
    """Cancel a task."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

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
    if not task:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Task cancel failed unexpectedly",
        )
    await db.commit()
    return _to_response(task)


# =============================================================================
# PROGRESS AND ARTIFACTS
# =============================================================================


@router.post("/{task_id}/progress", response_model=TaskResponse)
async def add_progress(
    task_id: UUID,
    data: ProgressRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> TaskResponse:
    """Add a progress update to a task."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

    # Only assigned agent can add progress
    if task.assigned_to != agent.agent_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the assigned agent can add progress updates",
        )

    task = await service.add_progress(
        task_id, agent.agent_id, data.message, data.percentage
    )
    if not task:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Add progress failed unexpectedly",
        )
    await db.commit()
    return _to_response(task)


@router.post("/{task_id}/checkpoint", response_model=TaskResponse)
async def add_checkpoint(
    task_id: UUID,
    data: CheckpointRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> TaskResponse:
    """Add a checkpoint for state recovery."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

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
    if not task:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Add checkpoint failed unexpectedly",
        )
    await db.commit()
    return _to_response(task)


@router.post("/{task_id}/commit", response_model=TaskResponse)
async def add_commit(
    task_id: UUID,
    data: CommitRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> TaskResponse:
    """Link a commit to a task."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

    # Only assigned agent can link commits
    if task.assigned_to != agent.agent_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the assigned agent can link commits",
        )

    task = await service.add_commit(task_id, data.hash, data.message, agent.agent_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Add commit failed unexpectedly",
        )
    await db.commit()
    return _to_response(task)
