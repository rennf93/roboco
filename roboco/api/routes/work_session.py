"""
WorkSession API Routes

CRUD and lifecycle operations for git work sessions.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from roboco.api.deps import (
    CurrentAgentContext,
    DbSession,
    require_developer_or_above,
    require_pm_or_above,
)
from roboco.api.schemas.work_session import (
    AddCommitRequest,
    AddFilesRequest,
    CreatePRRequest,
    UpdatePRStatusRequest,
    WorkSessionCreateRequest,
    WorkSessionResponse,
    WorkSessionSummaryResponse,
    session_to_response,
    session_to_summary,
)
from roboco.models import AgentRole
from roboco.models.permissions import AgentContext
from roboco.models.work_session import WorkSessionCreate, WorkSessionStatus
from roboco.services.work_session import WorkSessionService, get_work_session_service

router = APIRouter()


# =============================================================================
# OWNERSHIP GUARD
#
# Every mutating route keys off session_id alone, so without a re-check any
# developer could mutate a peer's session and any PM could merge any cell's PR
# — bypassing the verb layer's active-claimant gate. Re-assert the caller owns
# the session (dev ops) or owns the session's task cell (PM ops) before the
# service call (#158).
# =============================================================================


async def _assert_ownership(
    service: WorkSessionService,
    session_id: UUID,
    agent: AgentContext,
    *,
    pm_op: bool,
) -> None:
    """Fetch the session and verify the caller may mutate it.

    Raises 404 for a missing session, 403 for a wrong-owner / wrong-cell caller.
    Dev ops require the caller to BE the session's agent. PM ops (merge_pr)
    require a cell PM to own the session's task cell; main PM / CEO / board
    coordinate every cell and are admitted by the role gate alone.
    """
    session = await service.get(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Work session not found: {session_id}",
        )
    if pm_op:
        if agent.role == AgentRole.CELL_PM:
            team = await service.task_team_for_session(session_id)
            if agent.team is None or team is None or team != agent.team:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="cell PM does not own this session's task cell",
                )
    elif session.agent_id != agent.agent_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="not the owner of this work session",
        )


# =============================================================================
# LIST & GET ENDPOINTS
# =============================================================================


@router.get("", response_model=list[WorkSessionSummaryResponse])
async def list_sessions(
    db: DbSession,
    _agent: CurrentAgentContext,
    project_id: Annotated[UUID | None, Query(description="Filter by project")] = None,
    agent_id: Annotated[UUID | None, Query(description="Filter by agent")] = None,
    session_status: Annotated[
        WorkSessionStatus | None,
        Query(alias="status", description="Filter by status"),
    ] = None,
    active_only: Annotated[bool, Query(description="Only active sessions")] = False,
) -> list[WorkSessionSummaryResponse]:
    """
    List work sessions with optional filters.

    All agents can list sessions.
    """
    service = get_work_session_service(db)

    if active_only:
        sessions = await service.list_active_sessions(project_id=project_id)
    elif project_id:
        sessions = await service.list_by_project(project_id, status=session_status)
    elif agent_id:
        sessions = await service.list_by_agent(agent_id, status=session_status)
    else:
        # List active by default if no filters
        sessions = await service.list_active_sessions()

    return [session_to_summary(s) for s in sessions]


@router.get("/{session_id}", response_model=WorkSessionResponse)
async def get_session(
    session_id: UUID,
    db: DbSession,
    _agent: CurrentAgentContext,
) -> WorkSessionResponse:
    """Get a work session by ID."""
    service = get_work_session_service(db)

    session = await service.get(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Work session not found: {session_id}",
        )

    return session_to_response(session)


@router.get("/task/{task_id}", response_model=WorkSessionResponse | None)
async def get_active_session_for_task(
    task_id: UUID,
    db: DbSession,
    _agent: CurrentAgentContext,
) -> WorkSessionResponse | None:
    """Get the active work session for a task."""
    service = get_work_session_service(db)

    session = await service.get_active_for_task(task_id)
    if not session:
        return None

    return session_to_response(session)


# =============================================================================
# CREATE ENDPOINT
# =============================================================================


@router.post(
    "", response_model=WorkSessionResponse, status_code=status.HTTP_201_CREATED
)
async def create_session(
    data: WorkSessionCreateRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> WorkSessionResponse:
    """
    Create a new work session (Developer or PM).

    Typically created automatically when claiming a task.
    """
    require_developer_or_above(agent.role, "create work sessions")

    service = get_work_session_service(db)

    create_data = WorkSessionCreate(
        project_id=data.project_id,
        task_id=data.task_id,
        agent_id=agent.agent_id,
        branch_name=data.branch_name,
        base_branch=data.base_branch,
        target_branch=data.target_branch,
    )

    try:
        session = await service.create(create_data)
        await db.commit()
        return session_to_response(session)
    except Exception as e:
        await db.rollback()
        if "already" in str(e).lower():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e),
            ) from e
        raise


# =============================================================================
# COMMIT & FILE TRACKING
# =============================================================================


@router.post("/{session_id}/commits", response_model=WorkSessionResponse)
async def add_commit(
    session_id: UUID,
    data: AddCommitRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> WorkSessionResponse:
    """Add a commit to the work session."""
    require_developer_or_above(agent.role, "add commits")

    service = get_work_session_service(db)
    await _assert_ownership(service, session_id, agent, pm_op=False)

    session = await service.add_commit(session_id, data.commit_sha)
    await db.commit()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Work session not found: {session_id}",
        )

    return session_to_response(session)


@router.post("/{session_id}/files", response_model=WorkSessionResponse)
async def add_files_modified(
    session_id: UUID,
    data: AddFilesRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> WorkSessionResponse:
    """Add modified files to the work session."""
    require_developer_or_above(agent.role, "add files")

    service = get_work_session_service(db)
    await _assert_ownership(service, session_id, agent, pm_op=False)

    session = await service.add_files_modified(session_id, data.file_paths)
    await db.commit()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Work session not found: {session_id}",
        )

    return session_to_response(session)


# =============================================================================
# PR LIFECYCLE
# =============================================================================


@router.post("/{session_id}/pr", response_model=WorkSessionResponse)
async def create_pr(
    session_id: UUID,
    data: CreatePRRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> WorkSessionResponse:
    """Record PR creation for the work session."""
    require_developer_or_above(agent.role, "create PRs")

    service = get_work_session_service(db)
    await _assert_ownership(service, session_id, agent, pm_op=False)

    session = await service.create_pr(session_id, data.pr_number, data.pr_url)
    await db.commit()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Work session not found: {session_id}",
        )

    return session_to_response(session)


@router.patch("/{session_id}/pr", response_model=WorkSessionResponse)
async def update_pr_status(
    session_id: UUID,
    data: UpdatePRStatusRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> WorkSessionResponse:
    """Update the PR status."""
    require_developer_or_above(agent.role, "update PR status")

    service = get_work_session_service(db)
    await _assert_ownership(service, session_id, agent, pm_op=False)

    session = await service.update_pr_status(session_id, data.pr_status)
    await db.commit()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Work session not found: {session_id}",
        )

    return session_to_response(session)


@router.post("/{session_id}/pr/merge", response_model=WorkSessionResponse)
async def merge_pr(
    session_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
) -> WorkSessionResponse:
    """Record PR merge and complete the session (PM only).

    The merger is the AUTHENTICATED caller — never a client-supplied body value,
    which any PM could spoof to record the merge under another agent's id,
    corrupting the merge audit trail the completion/CEO-approval chain and
    metrics rely on (#271).
    """
    require_pm_or_above(agent.role, "merge PRs")

    service = get_work_session_service(db)
    await _assert_ownership(service, session_id, agent, pm_op=True)

    session = await service.merge_pr(session_id, agent.agent_id)
    await db.commit()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Work session not found: {session_id}",
        )

    return session_to_response(session)


# =============================================================================
# SESSION LIFECYCLE
# =============================================================================


@router.post("/{session_id}/complete", response_model=WorkSessionResponse)
async def complete_session(
    session_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
) -> WorkSessionResponse:
    """Mark the session as completed."""
    require_developer_or_above(agent.role, "complete sessions")

    service = get_work_session_service(db)
    await _assert_ownership(service, session_id, agent, pm_op=False)

    session = await service.complete(session_id)
    await db.commit()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Work session not found or not active: {session_id}",
        )

    return session_to_response(session)


@router.post("/{session_id}/abandon", response_model=WorkSessionResponse)
async def abandon_session(
    session_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
    reason: Annotated[str | None, Query(description="Reason for abandonment")] = None,
) -> WorkSessionResponse:
    """Abandon/cancel the work session."""
    require_developer_or_above(agent.role, "abandon sessions")

    service = get_work_session_service(db)
    await _assert_ownership(service, session_id, agent, pm_op=False)

    session = await service.abandon(session_id, reason=reason)
    await db.commit()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Work session not found or not active: {session_id}",
        )

    return session_to_response(session)
