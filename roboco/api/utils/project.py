"""
Project Route Helpers

Route-glue helpers backing roboco/api/routes/project.py.
"""

from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import HTTPException, status

from roboco.api.schemas.project import ConventionsActionResponse
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


def action_response(result: ScaffoldResult) -> ConventionsActionResponse:
    return ConventionsActionResponse(
        pr_number=result.pr_number, branch=result.branch, created=result.created
    )
