"""
Project API Routes

CRUD operations for managing git projects/repositories.
"""

from typing import TYPE_CHECKING, Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

if TYPE_CHECKING:
    from roboco.db.tables import ProjectTable

from roboco.api.deps import (
    CurrentAgentContext,
    DbSession,
    require_cell_access,
    require_pm_or_above,
)
from roboco.api.schemas.project import (
    ConventionFinding,
    ConventionsActionResponse,
    ConventionsHealthResponse,
    ConventionsResponse,
    ProjectCreateRequest,
    ProjectResponse,
    ProjectSummaryResponse,
    ProjectUpdateRequest,
    SetWorkspaceRequest,
    SyncStateRequest,
    project_to_response,
    project_to_summary,
)
from roboco.foundation.policy.conventions.models import ConventionsStandard
from roboco.models.base import Team
from roboco.models.project import ProjectCreate, ProjectUpdate
from roboco.services.conventions import (
    ScaffoldResult,
    get_conventions_service,
)
from roboco.services.project import ProjectService, get_project_service

router = APIRouter()


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
    require_pm_or_above(agent.role, "create projects")
    require_cell_access(agent, data.assigned_cell, "create")

    service = get_project_service(db)

    # If protected_branches wasn't provided, default to just the default_branch
    protected_branches = data.protected_branches
    if protected_branches is None:
        protected_branches = [data.default_branch]

    # Convert request to service model
    create_data = ProjectCreate(
        name=data.name,
        slug=data.slug,
        git_url=data.git_url,
        default_branch=data.default_branch,
        protected_branches=protected_branches,
        assigned_cell=data.assigned_cell,
        git_token=data.git_token,
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
    require_pm_or_above(agent.role, "update projects")

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

    require_cell_access(agent, project.assigned_cell, "update")

    # Convert request to service model
    update_data = ProjectUpdate(
        name=data.name,
        git_url=data.git_url,
        default_branch=data.default_branch,
        protected_branches=data.protected_branches,
        assigned_cell=data.assigned_cell,
        git_token=data.git_token,
        test_command=data.test_command,
        lint_command=data.lint_command,
        format_command=data.format_command,
        typecheck_command=data.typecheck_command,
        build_command=data.build_command,
        quality_command=data.quality_command,
        ci_watch_enabled=data.ci_watch_enabled,
        ci_watch_workflow=data.ci_watch_workflow,
        dep_update_command=data.dep_update_command,
        dep_update_paths=data.dep_update_paths,
        is_active=data.is_active,
    )

    updated = await service.update(project.id, update_data)
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
    require_pm_or_above(agent.role, "delete projects")

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

    require_cell_access(agent, project.assigned_cell, "delete")

    deleted = await service.delete(project.id)
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
    require_pm_or_above(agent.role, "set workspace")

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
        uuid = project.id

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
        uuid = project.id

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
    require_pm_or_above(agent.role, "manage access")

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
        uuid = project.id

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
    require_pm_or_above(agent.role, "manage access")

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
        uuid = project.id

    updated = await service.remove_allowed_agent(uuid, agent_id)
    await db.commit()

    if not updated:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project not found: {project_id}",
        )

    return project_to_response(updated)


# =============================================================================
# CONVENTIONS ENDPOINTS
# =============================================================================


async def _get_project_or_404(
    service: ProjectService, project_id: str
) -> "ProjectTable":
    """Resolve a project by UUID or slug, raising 404 when absent."""
    try:
        project = await service.get(UUID(project_id))
    except ValueError:
        project = await service.get_by_slug(project_id)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project not found: {project_id}",
        )
    return project


def _action_response(result: ScaffoldResult) -> ConventionsActionResponse:
    return ConventionsActionResponse(
        pr_number=result.pr_number, branch=result.branch, created=result.created
    )


@router.get("/{project_id}/conventions", response_model=ConventionsResponse)
async def get_conventions(
    project_id: str,
    db: DbSession,
    _agent: CurrentAgentContext,
) -> ConventionsResponse:
    """Return the project's effective conventions map + its current health."""
    project = await _get_project_or_404(get_project_service(db), project_id)
    conv = get_conventions_service(db)
    # Ensure a default-branch read clone once, then read the map + health from
    # it. This is the backfill: a project created before the standard existed
    # (no manual workspace_path) still resolves its committed conventions file.
    workspace = await conv.resolve_workspace(project)
    standard = await conv.get_map(project, workspace=workspace)
    health = await conv.health(project, workspace=workspace)
    await db.commit()
    return ConventionsResponse(
        standard=standard.model_dump(mode="json"),
        health=ConventionsHealthResponse(
            status=health.status,
            head_sha=health.head_sha,
            last_ok_sha=health.last_ok_sha,
        ),
    )


@router.put("/{project_id}/conventions", response_model=ConventionsActionResponse)
async def update_conventions(
    project_id: str,
    standard: ConventionsStandard,
    db: DbSession,
    agent: CurrentAgentContext,
) -> ConventionsActionResponse:
    """Commit an edited conventions standard back to the repo via a PR (PM+)."""
    require_pm_or_above(agent.role, "edit conventions")
    project = await _get_project_or_404(get_project_service(db), project_id)
    result = await get_conventions_service(db).commit_standard(project, standard)
    await db.commit()
    return _action_response(result)


@router.post(
    "/{project_id}/conventions/restore", response_model=ConventionsActionResponse
)
async def restore_conventions(
    project_id: str,
    db: DbSession,
    agent: CurrentAgentContext,
) -> ConventionsActionResponse:
    """Re-commit the conventions file from the last-good map via a PR (PM+)."""
    require_pm_or_above(agent.role, "restore conventions")
    project = await _get_project_or_404(get_project_service(db), project_id)
    result = await get_conventions_service(db).restore(project)
    await db.commit()
    return _action_response(result)


@router.get(
    "/{project_id}/conventions/findings",
    response_model=list[ConventionFinding],
)
async def get_conventions_findings(
    project_id: str,
    db: DbSession,
    _agent: CurrentAgentContext,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[ConventionFinding]:
    """Recent architectural-conventions findings for the project (violations feed)."""
    project = await _get_project_or_404(get_project_service(db), project_id)
    rows = await get_conventions_service(db).recent_findings(
        UUID(str(project.id)), limit
    )
    return [ConventionFinding(**row) for row in rows]
