"""
Task API Routes

Full CRUD operations and lifecycle management for tasks.
"""

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Body, HTTPException, Query, status

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
    CancelTaskRequest,
    CheckpointRequest,
    ClaimRequest,
    CommitRequest,
    CompleteTaskRequest,
    EscalateRequest,
    EscalateResponse,
    ProgressRequest,
    QANotes,
    SoftBlockRequest,
    SubstituteRequest,
    TaskCountResponse,
    TaskResponse,
    TaskSessionLinkResponse,
    TaskUpdate,
    TeamTasksQuery,
    enrich_task_with_context,
    task_list_to_response,
    task_to_response,
    transform_update_data,
)
from roboco.exceptions import TaskLifecycleError
from roboco.foundation.policy import task_completeness as tc
from roboco.models.base import AgentRole, TaskStatus, Team
from roboco.models.task import TaskCreate
from roboco.services.audit import get_audit_service
from roboco.services.base import (
    NotFoundError,
    ServiceError,
    UnauthorizedError,
    ValidationError,
)
from roboco.services.messaging import get_messaging_service
from roboco.services.notification_delivery import (
    EscalationError,
    get_notification_delivery_service,
)
from roboco.services.permissions import TaskAction
from roboco.services.task import (
    SoftBlockInput,
    TaskCreateRequest,
    extract_original_developer,
    get_task_service,
)
from roboco.utils.converters import require_uuid

router = APIRouter()

# Minimum character count for notes fields that must be substantive
# (QA pass notes, doc-complete notes, escalation notes). Below this the
# note is useless for the next reader, so the transition is refused.
_MIN_NOTES_CHARS = 20


def _translate_error(e: ServiceError) -> HTTPException:
    """Service errors → HTTP status. Kept at route layer; everything else moves."""
    if isinstance(e, NotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=e.message)
    if isinstance(e, UnauthorizedError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=e.message)
    if isinstance(e, ValidationError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=e.message)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=e.message
    )


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

    # `data.project_id` is `UUID` (required) on TaskCreate, so pydantic
    # rejects missing/null values with 422 before this handler runs.

    # Defense-in-depth completeness check. TaskCreate's Pydantic schema
    # already enforces the structural rules in TASK_AT_CREATE (min_length
    # on title/description/acceptance_criteria; the discriminator enums
    # for task_type/nature/estimated_complexity/team are required). What
    # Pydantic does NOT catch are the denylist phrases — placeholder ACs
    # like "completed and reviewed by assignee" or stub descriptions —
    # because those are well-formed strings. Re-running the canonical
    # checker here catches them at the route boundary, so route, schema,
    # and service all share one notion of "complete".
    completeness = tc.check(tc.TASK_AT_CREATE, data)
    if not completeness.passed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "incomplete_input",
                "missing": completeness.missing,
                "field_hints": completeness.field_hints,
            },
        )

    # Resolve assigned_to: accept either a UUID string or an agent slug
    # (e.g. "main-pm"). Slugs are how agents are addressed everywhere else
    # in the tooling, so requiring a raw UUID here was a paper cut.
    assigned_to_uuid: UUID | None = None
    if data.assigned_to:
        try:
            assigned_to_uuid = UUID(data.assigned_to)
        except ValueError:
            from roboco.services.repositories.query_helpers import (
                get_agent_by_slug,
            )

            agent_row = await get_agent_by_slug(db, data.assigned_to)
            if agent_row is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "error": {
                            "code": "ASSIGNEE_NOT_FOUND",
                            "message": (
                                f"No agent with slug or UUID '{data.assigned_to}'"
                            ),
                            "hint": "Use an agent slug (e.g. 'main-pm') or UUID",
                        }
                    },
                ) from None
            assigned_to_uuid = cast("UUID", agent_row.id)

    # Prompter origin tracking: enforce human confirmation gate so
    # LLM-drafted tasks cannot bypass review and enter the workflow.
    if data.source == "prompter" and not data.confirmed_by_human:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Prompter-originated tasks require human confirmation",
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
        assigned_to=assigned_to_uuid,
        target_date=data.target_date,
        estimated_complexity=data.estimated_complexity,
        nature=data.nature,
        status=data.status,
        sequence=data.sequence,  # Task ordering within siblings
        dependency_ids=data.dependency_ids,  # Dependencies for claim filtering
        # Git configuration (all tasks follow git workflow)
        task_type=data.task_type,
        project_id=data.project_id,
        product_id=data.product_id,
        # Prompter origin tracking
        source=data.source,
        confirmed_by_human=data.confirmed_by_human,
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


# Static-segment routes must be declared BEFORE `/{task_id}` so FastAPI
# matches the literal path instead of treating the segment as a UUID
# (which would 422 on these names).


@router.get("/awaiting-pm-review", response_model=list[TaskResponse])
async def get_awaiting_pm_review_tasks(
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
    team: Team | None = None,
) -> list[TaskResponse]:
    """Get tasks awaiting PM review."""
    service = get_task_service(db)

    # Apply team filter based on permissions
    can_view_all = permissions.can_perform_task_action(agent, TaskAction.VIEW_ALL)
    effective_team = team if can_view_all else agent.team

    tasks = await service.list_awaiting_pm_review(effective_team)
    return task_list_to_response(tasks)


@router.get("/awaiting-ceo-approval", response_model=list[TaskResponse])
async def get_awaiting_ceo_approval_tasks(
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
) -> list[TaskResponse]:
    """Get tasks awaiting CEO approval.

    CEO approval queue is org-wide (no team filter).
    Only visible to PMs and above.
    """
    # Only PMs and above can view the CEO approval queue
    can_view_all = permissions.can_perform_task_action(agent, TaskAction.VIEW_ALL)
    is_pm = agent.role in (AgentRole.CELL_PM, AgentRole.MAIN_PM)
    is_ceo = agent.role == AgentRole.CEO

    if not (can_view_all or is_pm or is_ceo):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only PMs and management can view CEO approval queue",
        )

    service = get_task_service(db)
    tasks = await service.list_awaiting_ceo_approval()
    return task_list_to_response(tasks)


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: UUID,
    db: DbSession,
) -> TaskResponse:
    """Get a specific task with full context (sessions, work session, project)."""
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

    # Enrich with work session and project context
    response = await enrich_task_with_context(response, db)

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

    # `status` is not a free-form field — it is an audited admin override so a
    # privileged operator can recover a task wedged in a state with no valid
    # in-band transition. Pop it out of the generic field update and apply it
    # through the audited path, gated on elevated permissions.
    new_status = updates.pop("status", None)

    task = await service.update(task_id, **updates)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Task update failed unexpectedly",
        )
    if new_status is not None and new_status != task.status:
        if not has_higher_perms:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Only privileged roles may override task status.",
            )
        task = await service.admin_set_status(
            task_id,
            new_status,
            actor_id=agent.agent_id,
            actor_role=getattr(agent, "role", None),
        )
        if not task:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Task status override failed unexpectedly",
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


@router.get("/{task_id}/descendants", response_model=list[TaskResponse])
async def get_descendants(
    task_id: UUID,
    db: DbSession,
) -> list[TaskResponse]:
    """Get ALL descendants of a task (recursive - children, grandchildren, etc.)."""
    service = get_task_service(db)
    tasks = await service.get_all_descendants(task_id)
    return task_list_to_response(tasks)


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
    """Claim a task (privileged roles may claim on behalf of another agent)."""
    service = get_task_service(db)
    try:
        task = await service.claim_task_for_agent(
            task_id,
            agent,
            permissions,
            claim_target_slug=(data.agent_id if data else None),
        )
    except ServiceError as e:
        raise _translate_error(e) from e
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

    # Field-level gates: must have a branch and (if claimed-first-time) a
    # plan. The service checks plan internally but returns a generic None
    # on failure; surface the specific cause here so the agent knows
    # what to call next.
    if not task.branch_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "NO_BRANCH: Task has no branch assigned. Unclaim and "
                "reclaim to regenerate the hierarchical branch, then "
                "start."
            ),
        )
    if task.status.value == "claimed" and not task.plan:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "NO_PLAN: Cannot start a claimed task without a plan. "
                "Set task.plan via PATCH /api/tasks/{id} (panel) or "
                "call gateway i_will_work_on(task_id, plan='...') (agents)."
            ),
        )

    # Pass agent_id and role for defense-in-depth validation in service layer
    task = await service.start(task_id, agent_id=agent.agent_id, agent_role=agent.role)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Cannot start task - invalid status (must be claimed, "
                "paused, or needs_revision)."
            ),
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
    if task.assigned_to != agent.agent_id and agent.role not in (
        AgentRole.CELL_PM,
        AgentRole.MAIN_PM,
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
    """Soft-block a task due to an external factor (not a task dependency)."""
    service = get_task_service(db)
    try:
        task = await service.soft_block_task_for_agent(
            task_id,
            agent,
            SoftBlockInput(
                blocker_type=data.blocker_type,
                reason=data.reason,
                what_needed=data.what_needed,
                resolver_type_raw=data.resolver_type,
            ),
        )
    except ServiceError as e:
        raise _translate_error(e) from e
    return task_to_response(task)


@router.post("/{task_id}/unblock", response_model=TaskResponse)
async def unblock_task(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
) -> TaskResponse:
    """Unblock a task and notify the assigned agent."""
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

    # Only assigned agent or PM can unblock a task
    if task.assigned_to != agent.agent_id and agent.role not in (
        AgentRole.CELL_PM,
        AgentRole.MAIN_PM,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to unblock this task",
        )

    # Remember the assigned agent before unblocking
    assigned_agent_id = task.assigned_to

    task = await service.unblock(task_id, agent.role)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot unblock task - not blocked",
        )

    # Notify the assigned agent that the task is unblocked
    if assigned_agent_id and assigned_agent_id != agent.agent_id:
        delivery = get_notification_delivery_service(db)
        await delivery.notify_assignee_of_unblock(
            task=task,
            task_id=task_id,
            from_agent_id=agent.agent_id,
            assignee_agent_id=require_uuid(assigned_agent_id),
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

    task = await service.pause(task_id, agent.role)
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

    task = await service.resume(task_id, agent.role)
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

    task = await service.submit_for_verification(task_id, agent.role)
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

    # Field-level gates: dev must have committed, pushed, opened a PR,
    # reported progress, and self-verified before QA can review. PR is
    # REQUIRED at this stage — QA reviews on GitHub, not in a raw
    # workspace diff. Without the pre-QA PR gate, the system falls into
    # needless QA-fail → dev-creates-PR-in-revision cycles (pure token
    # burn). A legitimate QA-fail (actual defect) is fine; a PR-missing
    # fail is always avoidable.
    if not task.self_verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "NOT_SELF_VERIFIED: Cannot submit for QA without a prior "
                "self-verification step. Call gateway i_am_done() "
                "(handles verification + QA submit), or for the panel "
                "POST /api/tasks/{id}/verify before /submit-qa."
            ),
        )
    if not task.commits:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "NO_COMMITS: Cannot submit for QA without at least one "
                "commit on this task. Use the roboco-do `commit(message, "
                "files)` verb before `i_am_done()` via gateway, or POST "
                "/api/tasks/{id}/submit-qa."
            ),
        )
    if task.pr_number is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "NO_PR: Cannot submit for QA without a PR. The PR is "
                "opened automatically by the choreographer when you call "
                "`submit_for_qa(task_id)` (gateway flow verb) — make sure "
                "you have at least one `commit(...)` on this task first "
                "so the choreographer has something to push."
            ),
        )
    if not task.progress_updates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "NO_PROGRESS: Cannot submit for QA without any "
                "progress updates. Make at least one commit() during "
                "execution — commit() auto-records a progress entry."
            ),
        )

    task = await service.submit_for_qa(task_id, agent.role)
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
    if agent.role != AgentRole.QA:
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

    # Defense-in-depth PR gate (submit_for_qa already blocks the no-PR
    # case). If a task reaches awaiting_qa without a PR for any reason
    # (legacy task, direct status manipulation), fail-qa with the note
    # below is the right move — don't silently pass.
    if task.pr_number is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "NO_PR_ATTACHED: Cannot pass QA without a PR on this "
                "task. Call gateway fail(task_id, issues=['PR not created'])"
                " or POST /api/tasks/{id}/fail-qa with the same issue, "
                "so the dev fixes it."
            ),
        )

    # QA pass requires notes summarizing what was verified; without
    # these, the dev can't learn from the review and the audit trail is
    # empty.
    if not data or not data.notes or len(data.notes.strip()) < _MIN_NOTES_CHARS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "QA_NOTES_REQUIRED: QA pass must include notes (>=20 "
                "chars) summarizing what was verified against the "
                "acceptance criteria. Call gateway pass(task_id, "
                "notes='...') or POST /api/tasks/{id}/pass-qa with "
                "notes set."
            ),
        )

    notes = data.notes
    task = await service.pass_qa(task_id, notes, agent.role)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot pass QA - invalid status for QA workflow",
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
    if agent.role != AgentRole.QA:
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

    task = await service.fail_qa(task_id, data.notes, agent.role)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot fail QA - invalid status for QA workflow",
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
    """
    # Audit: the documenter must record what was documented, so the next
    # reader knows what exists. No note → empty trail, so reject.
    if not data or not data.notes or len(data.notes.strip()) < _MIN_NOTES_CHARS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "DOC_NOTES_REQUIRED: docs-complete must include notes (>=20 "
                "chars) describing what was documented and where."
            ),
        )
    service = get_task_service(db)
    try:
        task = await service.docs_complete_for_task(task_id, agent, notes=data.notes)
    except ServiceError as e:
        raise _translate_error(e) from e
    return task_to_response(task)


@router.post("/{task_id}/submit-pm-review", response_model=TaskResponse)
async def submit_for_pm_review(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
    data: QANotes | None = None,
) -> TaskResponse:
    """Submit a task directly for PM review.

    Use this for tasks that don't follow the standard dev→QA→docs workflow,
    such as PM validation tasks, QA audit tasks, or other directly-assigned work.

    Only the assigned agent can submit their task for PM review.
    """
    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

    # Only assigned agent can submit for PM review
    if task.assigned_to != agent.agent_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the assigned agent can submit for PM review",
        )

    # Audit: the submitter must record what is ready for review.
    if not data or not data.notes or len(data.notes.strip()) < _MIN_NOTES_CHARS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "PM_REVIEW_NOTES_REQUIRED: submit-pm-review must include notes "
                "(>=20 chars) summarizing what is ready for the PM to review."
            ),
        )

    notes = data.notes
    task = await service.submit_for_pm_review(task_id, agent.role.value, notes)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot submit for PM review - task not in progress",
        )

    delivery = get_notification_delivery_service(db)
    await delivery.notify_pm_of_review_submission(
        task=task,
        task_id=task_id,
        submitter_agent_id=agent.agent_id,
        notes=notes,
    )
    await db.commit()
    return task_to_response(task)


@router.post("/{task_id}/complete", response_model=TaskResponse)
async def complete_task(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
    data: Annotated[CompleteTaskRequest | None, Body()] = None,
) -> TaskResponse:
    """Mark task as completed (PM only).

    Two completion paths:
    1. Developer work: task must be in awaiting_pm_review (went through QA/Docs)
    2. PM's own task: task can be in_progress if assigned to the completing PM

    PM Override for cancelled subtasks:
    If force_with_cancelled=True, PM can complete despite cancelled subtasks.
    Requires justification. Does NOT apply to pending/in_progress subtasks.
    """
    # Audit: completing a task is a decision that must carry its rationale.
    justification = data.justification if data else None
    if not justification or len(justification.strip()) < _MIN_NOTES_CHARS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "COMPLETE_JUSTIFICATION_REQUIRED: complete must include a "
                "justification (>=20 chars) recording why the task is done."
            ),
        )
    service = get_task_service(db)
    try:
        task = await service.complete_task_for_agent(
            task_id,
            agent,
            permissions,
            force_with_cancelled=(data.force_with_cancelled if data else False),
            justification=justification,
        )
    except ServiceError as e:
        raise _translate_error(e) from e
    return task_to_response(task)


@router.post("/{task_id}/cancel", response_model=TaskResponse)
async def cancel_task(
    task_id: UUID,
    data: CancelTaskRequest,
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
) -> TaskResponse:
    """Cancel a task. Reason is required for audit trail."""
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

    task = await service.cancel(
        task_id,
        agent_role=agent.role.value,
        cancellation_note=f"[CANCELLED by {agent.role.value}] {data.reason}",
    )
    if not task:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Task cancel failed unexpectedly",
        )
    await db.commit()
    return task_to_response(task)


# =============================================================================
# CEO APPROVAL WORKFLOW
# =============================================================================


@router.post("/{task_id}/escalate-to-ceo", response_model=TaskResponse)
async def escalate_to_ceo(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
    data: QANotes | None = None,
) -> TaskResponse:
    """Escalate a task to CEO for final approval (PM only).

    For major tasks that need CEO sign-off before merge: parent tasks
    with subtasks, high-priority features, breaking changes.
    """
    service = get_task_service(db)
    try:
        task = await service.escalate_to_ceo_for_agent(
            task_id, agent, permissions, notes=(data.notes if data else None)
        )
    except ServiceError as e:
        raise _translate_error(e) from e
    return task_to_response(task)


@router.post("/{task_id}/ceo-approve", response_model=TaskResponse)
async def ceo_approve_task(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
    data: QANotes | None = None,
) -> TaskResponse:
    """CEO approves and completes a task.

    Final approval step for major tasks. Only CEO can perform this action.
    """
    # Only CEO can approve
    if agent.role != AgentRole.CEO:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only CEO can approve tasks in CEO approval queue",
        )

    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

    # The CEO sign-off note is the audit record for merging to production —
    # it must be present and substantive. An approval with no rationale leaves
    # the audit trail empty, so reject it (the panel collects the note before
    # POSTing). Order mirrors pass-qa: 404 before the notes gate.
    if not data or not data.notes or len(data.notes.strip()) < _MIN_NOTES_CHARS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "CEO_NOTES_REQUIRED: CEO approval must include notes (>=20 "
                "chars) recording why the work is approved for production. "
                "POST /api/tasks/{id}/ceo-approve with notes='...'."
            ),
        )

    task = await service.ceo_approve(task_id, data.notes)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot approve - task not awaiting CEO approval",
        )

    await db.commit()
    return task_to_response(task)


@router.post("/{task_id}/approve-and-start", response_model=TaskResponse)
async def approve_and_start_task(
    task_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
    data: QANotes | None = None,
) -> TaskResponse:
    """CEO gate #1: approve a board-reviewed task and hand it to Main PM.

    Re-targets assigned_to -> main-pm while the task stays pending, so the
    orchestrator spawns Main PM. Only CEO; requires substantive notes.
    """
    if agent.role != AgentRole.CEO:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only CEO can approve-and-start tasks",
        )

    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

    # Order mirrors ceo-approve: 404 before the notes gate.
    if not data or not data.notes or len(data.notes.strip()) < _MIN_NOTES_CHARS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "START_NOTES_REQUIRED: approve-and-start must include notes "
                "(>=20 chars) recording why the board work is ready to build. "
                "POST /api/tasks/{id}/approve-and-start with notes='...'."
            ),
        )

    task = await service.approve_and_start(task_id, data.notes)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot start - task is not in a pending/startable state",
        )

    await db.commit()
    return task_to_response(task)


@router.post("/{task_id}/ceo-reject", response_model=TaskResponse)
async def ceo_reject_task(
    task_id: UUID,
    data: QANotes,
    db: DbSession,
    agent: CurrentAgentContext,
) -> TaskResponse:
    """CEO rejects a task and sends back for revision.

    Task goes back to NEEDS_REVISION status. Notes are required.
    """
    # Only CEO can reject
    if agent.role != AgentRole.CEO:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only CEO can reject tasks in CEO approval queue",
        )

    service = get_task_service(db)
    task = await service.get(task_id)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Task not found"
        )

    task = await service.ceo_reject(task_id, data.notes)
    if not task:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot reject - task not awaiting CEO approval",
        )

    # Notify original developer if reassigned
    if task.assigned_to:
        delivery = get_notification_delivery_service(db)
        await delivery.notify_assignee_of_ceo_rejection(
            task=task,
            task_id=task_id,
            from_agent_id=agent.agent_id,
            assignee_agent_id=require_uuid(task.assigned_to),
            notes=data.notes,
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

    delivery = get_notification_delivery_service(db)
    try:
        outcome = await delivery.escalate_and_notify(
            task=task,
            task_id=task_id,
            escalator_agent_id=agent.agent_id,
            reason=data.reason,
            explicit_target_slug=data.escalate_to,
        )
    except EscalationError as e:
        # Preserve the pre-refactor status-code mapping exactly:
        #   - missing escalator agent   -> 404 (agent lookup failure)
        #   - override rejected         -> 403 (chain violation)
        #   - no chain / target missing -> 400 (validation / config)
        detail = str(e)
        if detail.startswith("escalator agent"):
            http_code = status.HTTP_404_NOT_FOUND
        elif "Cannot escalate to" in detail:
            http_code = status.HTTP_403_FORBIDDEN
        else:
            http_code = status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=http_code, detail=detail) from e

    # BLOCKED (not PENDING) prevents the orchestrator from respawning the
    # original dev until the PM unblocks. Task state mutations live in
    # TaskService.apply_escalation — routes never touch task fields directly.
    await service.apply_escalation(
        task=task,
        target_agent_id=outcome.target_agent_id,
        escalator_slug=outcome.escalator_slug,
        target_slug=outcome.target_slug,
        reason=data.reason,
    )

    await db.commit()

    msg = (
        f"Task escalated to {outcome.target_slug} and set to BLOCKED. "
        f"PM will receive notification and must call gateway unblock(task_id) "
        "to provide guidance or reassign."
    )
    return EscalateResponse(
        status="escalated",
        task_id=task_id,
        escalated_to=outcome.target_slug,
        reason=data.reason,
        message=msg,
    )


# =============================================================================
# SUBSTITUTION (ALL AGENTS CAN SUBSTITUTE OUT)
# =============================================================================


@router.post("/{task_id}/substitute", response_model=TaskResponse)
async def substitute_task(
    task_id: UUID,
    data: SubstituteRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> TaskResponse:
    """Request to be substituted out of a task — graceful release.

    Bypasses the "can't claim while in_progress" rule. Reasons:
    `low_context`, `out_of_scope_team`, `out_of_scope_role`, `task_complete`,
    `max_retries`, `blocked_external`.
    """
    service = get_task_service(db)
    try:
        task = await service.substitute_task_for_agent(
            task_id, agent, reason_raw=data.reason, details=data.details
        )
    except ServiceError as e:
        raise _translate_error(e) from e
    return task_to_response(task)


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
        task = await service.activate(task_id, agent.role)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except TaskLifecycleError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
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
