"""
Agents API Schemas

Request/response models for agent endpoints.
"""

from typing import Any, cast
from uuid import UUID

from pydantic import BaseModel

from roboco.models import AgentRole, Team


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


def agent_to_response(agent: Any) -> AgentResponse:
    """AgentTable → AgentResponse mapper."""
    return AgentResponse(
        id=cast("UUID", agent.id),
        name=agent.name,
        slug=agent.slug,
        role=agent.role,
        team=agent.team,
    )
