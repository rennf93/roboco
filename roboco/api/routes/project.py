"""
Project API Routes

CRUD operations for managing git projects/repositories.
"""

from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from roboco.api.deps import CurrentAgentContext, DbSession
from roboco.api.schemas.project import (
    ProjectCreateRequest,
    ProjectResponse,
    ProjectSummaryResponse,
    ProjectUpdateRequest,
    SetWorkspaceRequest,
    SyncStateRequest,
    project_to_response,
    project_to_summary,
)
from roboco.models.base import Team
from roboco.models.project import ProjectCreate, ProjectUpdate
from roboco.services.project import get_project_service

router = APIRouter()


# =============================================================================
# PERMISSION HELPERS
# =============================================================================


def _require_pm_or_above(agent: CurrentAgentContext, action: str) -> None:
    """Require PM or higher role for an action."""
    allowed_roles = {"cell_pm", "main_pm", "product_owner", "auditor", "ceo"}
    if agent.role.value not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Only PMs and management can {action}",
        )


def _require_cell_access(
    agent: CurrentAgentContext,
    cell: Team,
    action: str,
) -> None:
    """Require agent to have access to a cell."""
    # Main PM, board, and CEO can access all cells
    global_roles = {"main_pm", "product_owner", "auditor", "ceo"}
    if agent.role.value in global_roles:
        return

    # Cell PMs can only manage their own cell
    if agent.team != cell:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Cannot {action} projects in {cell.value} cell",
        )


# =============================================================================
# LIST & GET ENDPOINTS
# =============================================================================


@router.get("", response_model=list[ProjectSummaryResponse])
async def list_projects(
    db: DbSession,
    _agent: CurrentAgentContext,
    cell: Annotated[Team | None, Query(description="Filter by cell")] = None,
    active_only: Annotated[bool, Query(description="Only active projects")] = True,
    limit: Annotated[int, Query(le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ProjectSummaryResponse]:
    """
    List projects with optional filters.

    All agents can list projects, but Cell PMs only see their cell's projects
    unless they have global access.
    """
    service = get_project_service(db)

    if cell:
        projects = await service.list_by_cell(cell, active_only=active_only)
    else:
        projects = await service.list_all(
            active_only=active_only,
            limit=limit,
            offset=offset,
        )

    return [project_to_summary(p) for p in projects]


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    db: DbSession,
    _agent: CurrentAgentContext,
) -> ProjectResponse:
    """
    Get project by ID or slug.

    Accepts either a UUID string or project slug (e.g., "roboco").
    """
    service = get_project_service(db)

    # Try UUID first, then slug
    try:
        uuid = UUID(project_id)
        project = await service.get(uuid)
    except ValueError:
        project = await service.get_by_slug(project_id)

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project not found: {project_id}",
        )

    return project_to_response(project)


# =============================================================================
# CREATE ENDPOINT
# =============================================================================


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    data: ProjectCreateRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> ProjectResponse:
    """
    Register a new project (PM only).

    Creates a project record for a git repository.
    The workspace must be cloned separately.
    """
    _require_pm_or_above(agent, "create projects")
    _require_cell_access(agent, data.assigned_cell, "create")

    service = get_project_service(db)

    # Convert request to service model
    create_data = ProjectCreate(
        name=data.name,
        slug=data.slug,
        git_url=data.git_url,
        default_branch=data.default_branch,
        protected_branches=data.protected_branches,
        assigned_cell=data.assigned_cell,
        test_command=data.test_command,
        lint_command=data.lint_command,
        format_command=data.format_command,
        typecheck_command=data.typecheck_command,
        build_command=data.build_command,
    )

    try:
        project = await service.create(create_data, created_by=agent.agent_id)
        await db.commit()
        return project_to_response(project)
    except Exception as e:
        await db.rollback()
        if "already exists" in str(e):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(e),
            ) from e
        raise


# =============================================================================
# UPDATE ENDPOINT
# =============================================================================


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    data: ProjectUpdateRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> ProjectResponse:
    """
    Update a project (PM only).

    Partial update - only provided fields are changed.
    """
    _require_pm_or_above(agent, "update projects")

    service = get_project_service(db)

    # Get project first to check cell access
    try:
        uuid = UUID(project_id)
        project = await service.get(uuid)
    except ValueError:
        project = await service.get_by_slug(project_id)

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project not found: {project_id}",
        )

    _require_cell_access(agent, project.assigned_cell, "update")

    # Convert request to service model
    update_data = ProjectUpdate(
        name=data.name,
        git_url=data.git_url,
        default_branch=data.default_branch,
        protected_branches=data.protected_branches,
        assigned_cell=data.assigned_cell,
        test_command=data.test_command,
        lint_command=data.lint_command,
        format_command=data.format_command,
        typecheck_command=data.typecheck_command,
        build_command=data.build_command,
        is_active=data.is_active,
    )

    updated = await service.update(cast("UUID", project.id), update_data)
    await db.commit()

    if not updated:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to update project",
        )

    return project_to_response(updated)


# =============================================================================
# DELETE ENDPOINT
# =============================================================================


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: str,
    db: DbSession,
    agent: CurrentAgentContext,
) -> None:
    """
    Delete a project (PM only).

    This removes the project registration. The actual git repository
    and workspace are not affected.
    """
    _require_pm_or_above(agent, "delete projects")

    service = get_project_service(db)

    # Get project first to check cell access
    try:
        uuid = UUID(project_id)
        project = await service.get(uuid)
    except ValueError:
        project = await service.get_by_slug(project_id)

    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project not found: {project_id}",
        )

    _require_cell_access(agent, project.assigned_cell, "delete")

    deleted = await service.delete(cast("UUID", project.id))
    await db.commit()

    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete project",
        )


# =============================================================================
# WORKSPACE MANAGEMENT
# =============================================================================


@router.post("/{project_id}/workspace", response_model=ProjectResponse)
async def set_workspace(
    project_id: str,
    data: SetWorkspaceRequest,
    db: DbSession,
    agent: CurrentAgentContext,
) -> ProjectResponse:
    """
    Set the local workspace path for a project (PM only).

    Called after cloning the repository to a local path.
    """
    _require_pm_or_above(agent, "set workspace")

    service = get_project_service(db)

    try:
        uuid = UUID(project_id)
    except ValueError:
        project = await service.get_by_slug(project_id)
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Project not found: {project_id}",
            ) from None
        uuid = cast("UUID", project.id)

    updated = await service.set_workspace_path(uuid, data.workspace_path)
    await db.commit()

    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project not found: {project_id}",
        )

    return project_to_response(updated)


@router.post("/{project_id}/sync", response_model=ProjectResponse)
async def update_sync_state(
    project_id: str,
    data: SyncStateRequest,
    db: DbSession,
    _agent: CurrentAgentContext,
) -> ProjectResponse:
    """
    Update the sync state after a git pull/fetch.

    Records the current HEAD commit and sync timestamp.
    """
    service = get_project_service(db)

    try:
        uuid = UUID(project_id)
    except ValueError:
        project = await service.get_by_slug(project_id)
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Project not found: {project_id}",
            ) from None
        uuid = cast("UUID", project.id)

    updated = await service.update_sync_state(uuid, data.head_commit)
    await db.commit()

    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project not found: {project_id}",
        )

    return project_to_response(updated)


# =============================================================================
# ACCESS CONTROL
# =============================================================================


@router.post("/{project_id}/access/{agent_id}", response_model=ProjectResponse)
async def add_agent_access(
    project_id: str,
    agent_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
) -> ProjectResponse:
    """
    Add an agent to the project's allowed list (PM only).

    By default, all agents in the assigned cell have access.
    This restricts access to specific agents.
    """
    _require_pm_or_above(agent, "manage access")

    service = get_project_service(db)

    try:
        uuid = UUID(project_id)
    except ValueError:
        project = await service.get_by_slug(project_id)
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Project not found: {project_id}",
            ) from None
        uuid = cast("UUID", project.id)

    updated = await service.add_allowed_agent(uuid, agent_id)
    await db.commit()

    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project not found: {project_id}",
        )

    return project_to_response(updated)


@router.delete("/{project_id}/access/{agent_id}", response_model=ProjectResponse)
async def remove_agent_access(
    project_id: str,
    agent_id: UUID,
    db: DbSession,
    agent: CurrentAgentContext,
) -> ProjectResponse:
    """
    Remove an agent from the project's allowed list (PM only).
    """
    _require_pm_or_above(agent, "manage access")

    service = get_project_service(db)

    try:
        uuid = UUID(project_id)
    except ValueError:
        project = await service.get_by_slug(project_id)
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Project not found: {project_id}",
            ) from None
        uuid = cast("UUID", project.id)

    updated = await service.remove_allowed_agent(uuid, agent_id)
    await db.commit()

    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project not found: {project_id}",
        )

    return project_to_response(updated)
