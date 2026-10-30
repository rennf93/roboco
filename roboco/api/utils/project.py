"""
Project Route Helpers

Route-glue helpers backing roboco/api/routes/project.py.
"""

from typing import TYPE_CHECKING
from typing import cast as typing_cast
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.api.schemas.project import (
    AllowedAgentSummary,
    ConventionsActionResponse,
    ProjectResponse,
    project_to_response,
)
from roboco.services.agent import get_agent_service
from roboco.services.conventions import ScaffoldResult
from roboco.services.project import ProjectService

if TYPE_CHECKING:
    from roboco.db.tables import ProjectTable


async def get_project_or_404(
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


async def resolve_allowed_agents(
    db: AsyncSession, project: "ProjectTable"
) -> list[AllowedAgentSummary] | None:
    """Resolve the project's raw allowed_agents ids to slug/name summaries.

    None (cell-default, today's whole-cell behavior) round-trips as None. A
    restricted list resolves each id via AgentService, silently dropping any
    id whose agent record no longer exists rather than erroring the response.
    """
    if project.allowed_agents is None:
        return None
    agents = await get_agent_service(db).list_by_ids(project.allowed_agents)
    return [
        AllowedAgentSummary(
            id=typing_cast("UUID", a.id), slug=str(a.slug), name=str(a.name)
        )
        for a in agents
    ]


async def build_project_response(
    db: AsyncSession, project: "ProjectTable"
) -> ProjectResponse:
    """project_to_response plus the DB-backed allowed_agents resolution."""
    response = project_to_response(project)
    response.allowed_agents = await resolve_allowed_agents(db, project)
    return response


def action_response(result: ScaffoldResult) -> ConventionsActionResponse:
    return ConventionsActionResponse(
        pr_number=result.pr_number, branch=result.branch, created=result.created
    )
