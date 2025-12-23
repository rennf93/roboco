"""
Groups API Schemas

Request/response models for group endpoints.
"""

from uuid import UUID

from pydantic import BaseModel, Field

from roboco.models.base import AgentRole


class GroupCreateRequest(BaseModel):
    """Request to create a group in a channel."""

    channel_slug: str = Field(
        ...,
        description="Channel slug where group will be created",
        examples=["backend-cell", "dev-all"],
    )
    name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Group name",
        examples=["User Preferences Feature", "Sprint 12 Work"],
    )
    hierarchy_level: int = Field(
        default=4,
        ge=0,
        le=4,
        description=(
            "Access level: 0=CEO, 1=Board, 2=Main PM, 3=Cell PM, 4=Cell Members"
        ),
    )
    allowed_roles: list[AgentRole] | None = Field(
        default=None,
        description="Specific roles that can access (overrides hierarchy)",
    )


class GroupResponse(BaseModel):
    """Response for a created/retrieved group."""

    id: UUID
    name: str
    channel_id: UUID
    channel_slug: str
    hierarchy_level: int
    is_active: bool
    total_sessions: int = 0
    total_messages: int = 0
    active_session_id: UUID | None = None


class GroupDetailResponse(GroupResponse):
    """Detailed group response with additional fields."""

    allowed_roles: list[str]
    members: list[UUID]
