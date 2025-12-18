"""
Agents API Schemas

Request/response models for agent endpoints.
"""

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
