"""
Agent Routes

Provides agent lookup and information endpoints.
"""

from typing import cast
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select

from roboco.api.deps import DbSession
from roboco.db.tables import AgentTable
from roboco.models import AgentRole, Team

router = APIRouter()


class AgentResponse(BaseModel):
    """Response model for agent information."""

    id: UUID
    name: str
    slug: str
    role: AgentRole
    team: Team | None

    class Config:
        """Pydantic config."""

        from_attributes = True


@router.get("")
async def list_agents(
    db: DbSession,
    slug: str | None = Query(None, description="Filter by agent slug"),
    role: str | None = Query(None, description="Filter by role"),
    team: str | None = Query(None, description="Filter by team"),
) -> list[AgentResponse]:
    """
    List agents with optional filters.

    Supports filtering by slug, role, or team.
    """
    query = select(AgentTable)

    if slug:
        query = query.where(AgentTable.slug == slug)
    if role:
        try:
            role_enum = AgentRole(role.lower())
            query = query.where(AgentTable.role == role_enum)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid role: {role}",
            ) from None
    if team:
        try:
            team_enum = Team(team.lower())
            query = query.where(AgentTable.team == team_enum)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid team: {team}",
            ) from None

    result = await db.execute(query)
    agents = result.scalars().all()

    return [
        AgentResponse(
            id=cast("UUID", agent.id),
            name=agent.name,
            slug=agent.slug,
            role=agent.role,
            team=agent.team,
        )
        for agent in agents
    ]


@router.get("/{agent_id}")
async def get_agent(
    agent_id: str,
    db: DbSession,
) -> AgentResponse:
    """
    Get agent by ID (UUID or slug).

    Accepts either a UUID string or agent slug (e.g., "be-dev-1").
    """
    # Try to parse as UUID first
    try:
        uuid = UUID(agent_id)
        result = await db.execute(
            select(AgentTable).where(AgentTable.id == uuid)
        )
    except ValueError:
        # Not a UUID, try slug lookup
        result = await db.execute(
            select(AgentTable).where(AgentTable.slug == agent_id)
        )

    agent = result.scalar_one_or_none()

    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent not found: {agent_id}",
        )

    return AgentResponse(
        id=cast("UUID", agent.id),
        name=agent.name,
        slug=agent.slug,
        role=agent.role,
        team=agent.team,
    )
