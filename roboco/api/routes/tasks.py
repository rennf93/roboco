"""
Task API Routes

Full CRUD operations and lifecycle management for tasks.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Query, status
from sqlalchemy import select

from roboco.agents_config import get_escalation_target
from roboco.api.deps import (
    CurrentAgentContext,
    DbSession,
    PermissionServiceDep,
    get_permission_service,
)
from roboco.api.schemas.sessions import (
    SessionTaskLinkResponse,
    TaskSessionsResponse,
)
from roboco.api.schemas.tasks import (
    CheckpointRequest,
    ClaimRequest,
    CommitRequest,
    EscalateRequest,
    EscalateResponse,
    ProgressRequest,
    QANotes,
    SoftBlockRequest,
    TaskCountResponse,
    TaskResponse,
    TaskSessionLinkResponse,
    TaskUpdate,
    TeamTasksQuery,
    task_list_to_response,
    task_to_response,
    transform_update_data,
)
from roboco.db.tables import AgentTable, NotificationTable
from roboco.models.base import TaskStatus, Team
from roboco.models.task import TaskCreate
from roboco.services.audit import get_audit_service
from roboco.services.messaging import get_messaging_service
from roboco.services.permissions import TaskAction
from roboco.services.task import (
    TaskCreateRequest,
    extract_original_developer,
    get_task_service,
)
from roboco.utils.converters import require_uuid

router = APIRouter()


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
        assigned_to=data.assigned_to,
        target_date=data.target_date,
        estimated_complexity=data.estimated_complexity,
        status=data.status,
    )
    task = await service.create(req)
    await db.commit()
    return task_to_response(task)


@router.get("", response_model=list[TaskResponse])
async def list_tasks(
    db: DbSession,
    agent: CurrentAgentContext,
    team: Team | None = None,
    status: TaskStatus | None = None,
    limit: int = Query(100, ge=1, le=500),
) -> list[TaskResponse]:
    """
    List tasks with optional filters.

    View permissions:
    - Main PM, Board, Auditor: Can see all tasks
    - Cell PM: Can see own cell's tasks
    - Cell members: Can only see own cell's tasks
    """
    service = get_task_service(db)
    permissions = get_permission_service()

    # Determine effective team filter based on permissions
    can_view_all = permissions.can_perform_task_action(agent, TaskAction.VIEW_ALL)
    effective_team = team

    if not can_view_all:
        # Cell members can only see their own team's tasks
        if agent.team:
            effective_team = agent.team
        else:
            # No team assigned - return empty list
            return []

    if effective_team and status:
        tasks = await service.list_by_team(effective_team, status, limit)
    elif effective_team:
        tasks = await service.list_by_team(effective_team, limit=limit)
    elif status:
        tasks = await service.list_by_status(status)
    else:
        tasks = await service.list_all(limit)

    return task_list_to_response(tasks)


@router.get("/my", response_model=list[TaskResponse])
async def get_my_tasks(
    db: DbSession,
    agent: CurrentAgentContext,
    status: TaskStatus | None = None,
) -> list[TaskResponse]:
    """Get tasks assigned to the current agent."""
    service = get_task_service(db)
    tasks = await service.list_by_assignee(agent.agent_id, status)
    return task_list_to_response(tasks)


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
    return task_list_to_response(tasks)


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
    return task_list_to_response(tasks)


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
    return task_list_to_response(tasks)


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
    return task_list_to_response(tasks)


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
    return task_list_to_response(tasks)


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
    """Get a specific task with linked sessions."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

    # Get linked sessions for this task
    messaging = get_messaging_service(db)
    session_links = await messaging.get_sessions_for_task(task_id)

    # Build response with sessions
    response = task_to_response(task)
    response.sessions = [
        TaskSessionLinkResponse(
            session_id=require_uuid(link.session_id),
            channel_slug=link.session.group.channel.slug,
            scope=link.session.scope,
            is_primary=link.is_primary,
            relationship_type=link.relationship_type,
        )
        for link in session_links
        if link.session and link.session.group and link.session.group.channel
    ]

    return response


@router.put("/{task_id}", response_model=TaskResponse)
@router.patch("/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: UUID,
    data: TaskUpdate,
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
) -> TaskResponse:
    """Update a task. Supports both PUT and PATCH for partial updates.

    CEO and privileged roles can update any field including:
    - Basic info (title, description, acceptance_criteria, priority, etc.)
    - Ownership (team, assigned_to)
    - Relationships (parent_task_id, dependency_ids, blocker_ids)
    - Planning (plan with sub_tasks, risks, open_questions)
    - Execution tracking (progress_updates, checkpoints)
    - Artifacts (commits)
    - Notes (dev_notes, qa_notes, auditor_notes, quick_context)
    """
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

    # Transform input data for database storage
    updates = transform_update_data(data)

    task = await service.update(task_id, **updates)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Task update failed unexpectedly",
        )
    await db.commit()
    return task_to_response(task)


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
    return task_list_to_response(tasks)


# =============================================================================
# LIFECYCLE ENDPOINTS
# =============================================================================


async def _resolve_claim_agent_id(db: DbSession, agent_id_str: str) -> UUID:
    """Resolve agent ID from UUID string or slug."""
    try:
        return UUID(agent_id_str)
    except ValueError:
        pass

    result = await db.execute(
        select(AgentTable.id).where(AgentTable.slug == agent_id_str)
    )
    agent_uuid = result.scalar_one_or_none()
    if not agent_uuid:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id_str}",
        )
    return UUID(str(agent_uuid))


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
    can_assign = permissions.can_perform_task_action(
        agent, TaskAction.ASSIGN, task.team
    )
    claim_agent_id = agent.agent_id
    if data and data.agent_id and can_assign:
        claim_agent_id = await _resolve_claim_agent_id(db, data.agent_id)

    # Allow reassignment if PM is assigning on behalf of another agent
    allow_reassign = bool(can_assign and data and data.agent_id is not None)
    task = await service.claim(task_id, claim_agent_id, allow_reassign=allow_reassign)
    if not task:
        status_msg = "not pending or claimed" if allow_reassign else "not pending"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot claim task - {status_msg}",
        )
    await db.commit()
    return task_to_response(task)


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
    return task_to_response(task)


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
    return task_to_response(task)


@router.post("/{task_id}/soft-block", response_model=TaskResponse)
async def soft_block_task(
    task_id: UUID,
    data: SoftBlockRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> TaskResponse:
    """Soft-block a task due to an external factor (not a task dependency).

    Use this when blocked by:
    - External dependencies (waiting for API access, credentials)
    - Questions that need PM/stakeholder input
    - Technical blockers (infrastructure issues)

    For blocking due to another task, use the /block endpoint instead.
    """
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

    task = await service.soft_block(
        task_id, data.reason, data.blocker_type, data.what_needed
    )
    if not task:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot block task - must be in_progress",
        )
    await db.commit()
    return task_to_response(task)


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
    return task_to_response(task)


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
    return task_to_response(task)


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
    return task_to_response(task)


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
    return task_to_response(task)


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
    return task_to_response(task)


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
    # Check against original developer stored in quick_context, not current assigned_to
    original_dev = extract_original_developer(task.quick_context)

    if original_dev and str(agent.agent_id) == original_dev:
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
    return task_to_response(task)


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
    # Check against original developer stored in quick_context, not current assigned_to
    original_dev = extract_original_developer(task.quick_context)

    if original_dev and str(agent.agent_id) == original_dev:
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
    return task_to_response(task)


@router.post("/{task_id}/docs-complete", response_model=TaskResponse)
async def docs_complete(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
    data: QANotes | None = None,
) -> TaskResponse:
    """Mark documentation as complete (documenter only).

    Transitions task from awaiting_documentation to awaiting_pm_review.
    The Cell PM will then review and complete the task.
    """
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

    # Only documenter role can mark docs complete
    if agent.role.value != "documenter":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only documenters can mark documentation as complete",
        )

    # Documenter cannot document their own work (self-review prevention)
    original_dev = extract_original_developer(task.quick_context)
    if original_dev and str(agent.agent_id) == original_dev:
        audit = get_audit_service()
        await audit.log_task_action_denial(
            agent_id=agent.agent_id,
            agent_role=agent.role.value,
            task_id=task_id,
            action="docs_complete",
            reason="Self-documentation not permitted",
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Cannot document your own task",
        )

    doc_notes = data.notes if data else None
    task = await service.docs_complete(task_id, doc_notes)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot mark docs complete - task not awaiting documentation",
        )
    await db.commit()
    return task_to_response(task)


@router.post("/{task_id}/complete", response_model=TaskResponse)
async def complete_task(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
) -> TaskResponse:
    """Mark task as completed (PM only).

    Two completion paths:
    1. Developer work: task must be in awaiting_pm_review (went through QA/Docs)
    2. PM's own task: task can be in_progress if assigned to the completing PM
    """
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

    # Only PMs can complete tasks
    can_close = permissions.can_perform_task_action(agent, TaskAction.CLOSE, task.team)
    if not can_close:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only PMs can complete tasks",
        )

    # Check for incomplete subtasks before completing parent
    subtasks = await service.get_subtasks(task_id)
    incomplete_subtasks = [
        st
        for st in subtasks
        if st.status not in (TaskStatus.COMPLETED, TaskStatus.CANCELLED)
    ]
    if incomplete_subtasks:
        max_titles_shown = 3
        incomplete_titles = [st.title for st in incomplete_subtasks[:max_titles_shown]]
        detail = (
            f"Cannot complete task - {len(incomplete_subtasks)} subtask(s) "
            f"still pending: {', '.join(incomplete_titles)}"
        )
        if len(incomplete_subtasks) > max_titles_shown:
            detail += f" (+{len(incomplete_subtasks) - max_titles_shown} more)"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=detail,
        )

    # Pass agent_id so service can check if PM is completing their own task
    task = await service.complete(task_id, agent_id=agent.agent_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot complete task - must be in awaiting_pm_review or "
            "in_progress (if your own task)",
        )
    await db.commit()
    return task_to_response(task)


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
    return task_to_response(task)


# =============================================================================
# ESCALATION (ALL AGENTS CAN ESCALATE)
# =============================================================================


@router.post("/{task_id}/escalate", response_model=EscalateResponse)
async def escalate_task(
    task_id: UUID,
    data: EscalateRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> EscalateResponse:
    """
    Escalate a task to PM/management.

    IMPORTANT: Unlike normal notifications, escalation is available to ALL agents.
    This is a critical workflow tool for getting help when blocked.
    Permission checks are intentionally bypassed for escalation.

    Escalation chain:
    - Developers → Cell PM
    - QA → Cell PM
    - Documenters → Cell PM
    - Cell PM → Main PM
    - Main PM → Product Owner
    - Product Owner → CEO
    """
    # Verify task exists
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

    # Get the agent's slug for escalation chain lookup
    agent_result = await db.execute(
        select(AgentTable).where(AgentTable.id == agent.agent_id)
    )
    agent_record = agent_result.scalar_one_or_none()
    if not agent_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found"
        )

    # Determine escalation target
    target_slug = data.escalate_to or get_escalation_target(agent_record.slug)
    if not target_slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"No escalation target configured for {agent_record.slug}",
        )

    # Resolve target agent UUID
    target_result = await db.execute(
        select(AgentTable).where(AgentTable.slug == target_slug)
    )
    target_agent = target_result.scalar_one_or_none()
    if not target_agent:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Escalation target not found: {target_slug}",
        )

    # Create escalation notification directly (bypassing permission checks)
    body = (
        f"Task {task_id} escalated by {agent_record.slug}.\n\n"
        f"Reason: {data.reason}"
    )
    notification = NotificationTable(
        type="blocker_escalation",
        priority="high",
        from_agent=agent.agent_id,
        to_agents=[target_agent.id],
        subject=f"Escalation: {task.title or 'Unknown task'}",
        body=body,
        related_task_id=task_id,
        requires_ack=True,
        read_by=[],
        acked_by=[],
    )
    db.add(notification)
    await db.commit()

    msg = (
        f"Task escalated to {target_slug}. "
        "They will be notified and can reassign or provide guidance."
    )
    return EscalateResponse(
        status="escalated",
        task_id=task_id,
        escalated_to=target_slug,
        reason=data.reason,
        message=msg,
    )


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
    return task_to_response(task)


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
    return task_to_response(task)


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
    return task_to_response(task)


# =============================================================================
# TASK ACTIVATION (PM ONLY)
# =============================================================================


@router.post("/{task_id}/activate", response_model=TaskResponse)
async def activate_task(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
) -> TaskResponse:
    """
    Activate a task from BACKLOG to PENDING status (PM only).

    This is the final step in PM setup. After creating a session and
    linking the task, the PM activates it to make it ready for work.

    REQUIRES: Task must have at least one linked session.
    """
    # Check PM permission (CREATE permission required for activation)
    if not permissions.can_perform_task_action(agent, TaskAction.CREATE):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only PMs and management can activate tasks",
        )

    service = get_task_service(db)

    try:
        task = await service.activate(task_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    await db.commit()
    return task_to_response(task)


# =============================================================================
# SESSION-TASK ENDPOINTS
# =============================================================================


@router.get("/{task_id}/sessions", response_model=TaskSessionsResponse)
async def get_sessions_for_task(
    task_id: UUID,
    db: DbSession,
    _agent: CurrentAgentContext,  # Kept for auth dependency
) -> TaskSessionsResponse:
    """Get all sessions linked to a task."""
    # Verify task exists
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Task not found",
        )

    # Get sessions
    messaging = get_messaging_service(db)
    links = await messaging.get_sessions_for_task(task_id)

    # Find primary session
    primary_link = next((link for link in links if link.is_primary), None)

    return TaskSessionsResponse(
        task_id=task_id,
        sessions=[
            SessionTaskLinkResponse(
                id=require_uuid(link.id),
                session_id=require_uuid(link.session_id),
                task_id=require_uuid(link.task_id),
                is_primary=link.is_primary,
                relationship_type=link.relationship_type,
                added_at=link.added_at,
                added_by=require_uuid(link.added_by) if link.added_by else None,
            )
            for link in links
        ],
        primary_session_id=(
            require_uuid(primary_link.session_id) if primary_link else None
        ),
    )
