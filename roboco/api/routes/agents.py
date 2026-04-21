"""
Agent Routes

Thin HTTP plumbing over `AgentService`: validate inputs, convert
`NotFoundError` to 404, shape responses. No DB access in this module.
"""

from typing import TYPE_CHECKING, cast

from fastapi import APIRouter, HTTPException, Query, status

from roboco.api.deps import DbSession
from roboco.api.schemas.agents import AgentResponse
from roboco.models import AgentRole, Team
from roboco.services.agent import get_agent_service
from roboco.services.base import NotFoundError

if TYPE_CHECKING:
    from uuid import UUID

    from roboco.db.tables import AgentTable

router = APIRouter()


def _to_response(agent: "AgentTable") -> AgentResponse:
    return AgentResponse(
        id=cast("UUID", agent.id),
        name=agent.name,
        slug=agent.slug,
        role=agent.role,
        team=agent.team,
    )


@router.get("")
async def list_agents(
    db: DbSession,
    slug: str | None = Query(None, description="Filter by agent slug"),
    role: str | None = Query(None, description="Filter by role"),
    team: str | None = Query(None, description="Filter by team"),
) -> list[AgentResponse]:
    """List agents with optional slug / role / team filters."""
    role_enum: AgentRole | None = None
    if role:
        try:
            role_enum = AgentRole(role.lower())
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid role: {role}",
            ) from e

    team_enum: Team | None = None
    if team:
        try:
            team_enum = Team(team.lower())
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid team: {team}",
            ) from e

    service = get_agent_service(db)
    agents = await service.list_agents(slug=slug, role=role_enum, team=team_enum)
    return [_to_response(a) for a in agents]


@router.get("/{agent_id}")
async def get_agent(
    agent_id: str,
    db: DbSession,
) -> AgentResponse:
    """Get an agent by UUID or slug."""
    service = get_agent_service(db)
    try:
        agent = await service.get_by_uuid_or_slug_or_raise(agent_id)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return _to_response(agent)
