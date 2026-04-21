"""
Channels API Schemas

Request/response models for channel endpoints.
"""

from typing import TYPE_CHECKING
from uuid import UUID

from fastapi import HTTPException, status
from pydantic import BaseModel, Field

from roboco.models import AgentRole, ChannelType

if TYPE_CHECKING:
    from roboco.services.permissions import AgentContext


class ChannelResponse(BaseModel):
    """Channel response with computed fields."""

    id: UUID
    name: str
    slug: str
    type: ChannelType
    description: str | None
    topic: str | None
    member_count: int
    message_count: int
    group_count: int
    is_archived: bool
    is_private: bool
    can_write: bool  # Whether current agent can write


class ChannelListResponse(BaseModel):
    """Paginated list of channels."""

    items: list[ChannelResponse]
    total: int
    page: int
    page_size: int


class ChannelDetailResponse(ChannelResponse):
    """Detailed channel response with groups."""

    groups: list[dict]


class GroupResponse(BaseModel):
    """Group within a channel."""

    id: UUID
    name: str
    hierarchy_level: int
    is_active: bool
    total_messages: int
    active_session_id: UUID | None = None


class ListChannelsQuery(BaseModel):
    """Query params for listing channels."""

    slug: str | None = Field(None, description="Filter by channel slug")
    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)
    include_archived: bool = False


# =============================================================================
# HELPERS AND UTILITIES
# =============================================================================

# Roles authorized to manage channels
CHANNEL_ADMIN_ROLES = frozenset(
    {AgentRole.CEO, AgentRole.PRODUCT_OWNER, AgentRole.MAIN_PM}
)


def require_channel_admin(agent: "AgentContext") -> None:
    """Raise 403 if agent is not authorized to manage channels."""
    if agent.role not in CHANNEL_ADMIN_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to manage channels",
        )
