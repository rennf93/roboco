"""
Channel Routes

CRUD operations for communication channels.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from roboco.api.deps import CurrentAgentContext, DbSession, PermissionServiceDep
from roboco.db.tables import ChannelTable
from roboco.models import AgentRole, ChannelCreate, ChannelType, ChannelUpdate
from roboco.utils.converters import require_uuid, to_python_uuid

router = APIRouter()


# =============================================================================
# Response Models
# =============================================================================


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


# =============================================================================
# Query Parameter Models
# =============================================================================


class ListChannelsQuery(BaseModel):
    """Query params for listing channels."""

    page: int = Field(1, ge=1)
    page_size: int = Field(20, ge=1, le=100)
    include_archived: bool = False


# =============================================================================
# Routes
# =============================================================================


@router.get(
    "",
    response_model=ChannelListResponse,
    summary="List channels",
    description="List all channels accessible to the current agent.",
)
async def list_channels(
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
    params: Annotated[ListChannelsQuery, Query()],
) -> ChannelListResponse:
    """List channels the agent can access."""
    # Get accessible channels based on permissions
    accessible_slugs = permissions.get_accessible_channels(agent)

    # Query channels by slug
    query = select(ChannelTable).where(ChannelTable.slug.in_(accessible_slugs))

    if not params.include_archived:
        query = query.where(ChannelTable.is_archived.is_(False))

    # Get total count
    count_query = select(ChannelTable.id).where(ChannelTable.slug.in_(accessible_slugs))
    if not params.include_archived:
        count_query = count_query.where(ChannelTable.is_archived.is_(False))
    count_result = await db.execute(count_query)
    total = len(count_result.all())

    # Apply pagination
    query = query.offset((params.page - 1) * params.page_size).limit(params.page_size)
    query = query.order_by(ChannelTable.name)

    result = await db.execute(query)
    channels = result.scalars().all()

    items = [
        ChannelResponse(
            id=require_uuid(ch.id),
            name=ch.name,
            slug=ch.slug,
            type=ch.type,
            description=ch.description,
            topic=ch.topic,
            member_count=len(ch.members),
            message_count=ch.message_count,
            group_count=ch.group_count,
            is_archived=ch.is_archived,
            is_private=ch.is_private,
            can_write=permissions.can_write_channel(agent, ch.slug),
        )
        for ch in channels
    ]

    return ChannelListResponse(
        items=items,
        total=total,
        page=params.page,
        page_size=params.page_size,
    )


@router.get(
    "/{channel_id}",
    response_model=ChannelDetailResponse,
    summary="Get channel",
    description="Get detailed information about a channel.",
)
async def get_channel(
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
    channel_id: UUID,
) -> ChannelDetailResponse:
    """Get channel details."""
    query = (
        select(ChannelTable)
        .where(ChannelTable.id == channel_id)
        .options(selectinload(ChannelTable.groups))
    )

    result = await db.execute(query)
    channel = result.scalar_one_or_none()

    if not channel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Channel not found",
        )

    # Check access using permission service
    if not permissions.can_read_channel(agent, channel.slug):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this channel",
        )

    groups = [
        {
            "id": str(g.id),
            "name": g.name,
            "hierarchy_level": g.hierarchy_level,
            "is_active": g.is_active,
            "total_messages": g.total_messages,
        }
        for g in channel.groups
    ]

    return ChannelDetailResponse(
        id=require_uuid(channel.id),
        name=channel.name,
        slug=channel.slug,
        type=channel.type,
        description=channel.description,
        topic=channel.topic,
        member_count=len(channel.members),
        message_count=channel.message_count,
        group_count=channel.group_count,
        is_archived=channel.is_archived,
        is_private=channel.is_private,
        can_write=permissions.can_write_channel(agent, channel.slug),
        groups=groups,
    )


@router.get(
    "/{channel_id}/groups",
    response_model=list[GroupResponse],
    summary="Get channel groups",
    description="Get all groups in a channel.",
)
async def get_channel_groups(
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
    channel_id: UUID,
) -> list[GroupResponse]:
    """Get all groups in a channel."""
    query = (
        select(ChannelTable)
        .where(ChannelTable.id == channel_id)
        .options(selectinload(ChannelTable.groups))
    )

    result = await db.execute(query)
    channel = result.scalar_one_or_none()

    if not channel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Channel not found",
        )

    # Check access using permission service
    if not permissions.can_read_channel(agent, channel.slug):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You don't have access to this channel",
        )

    return [
        GroupResponse(
            id=require_uuid(g.id),
            name=g.name,
            hierarchy_level=g.hierarchy_level,
            is_active=g.is_active,
            total_messages=g.total_messages,
            active_session_id=to_python_uuid(g.active_session_id),
        )
        for g in channel.groups
    ]


@router.post(
    "",
    response_model=ChannelResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create channel",
    description="Create a new channel. Requires admin privileges.",
)
async def create_channel(
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
    data: ChannelCreate,
) -> ChannelResponse:
    """Create a new channel."""
    # Only Board, Main PM can create channels
    allowed_roles = {AgentRole.CEO, AgentRole.PRODUCT_OWNER, AgentRole.MAIN_PM}
    if agent.role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to create channels",
        )

    # Check if slug already exists
    existing = await db.execute(
        select(ChannelTable).where(ChannelTable.slug == data.slug)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Channel with slug '{data.slug}' already exists",
        )

    # Create channel
    channel = ChannelTable(
        name=data.name,
        slug=data.slug,
        type=data.type,
        description=data.description,
        members=list(data.members),
        writers=list(data.writers),
        silent_observers=list(data.silent_observers),
        is_private=data.is_private,
    )

    db.add(channel)
    await db.flush()

    return ChannelResponse(
        id=require_uuid(channel.id),
        name=channel.name,
        slug=channel.slug,
        type=channel.type,
        description=channel.description,
        topic=channel.topic,
        member_count=len(channel.members),
        message_count=0,
        group_count=0,
        is_archived=False,
        is_private=channel.is_private,
        can_write=permissions.can_write_channel(agent, channel.slug),
    )


@router.patch(
    "/{channel_id}",
    response_model=ChannelResponse,
    summary="Update channel",
    description="Update channel settings.",
)
async def update_channel(
    db: DbSession,
    agent: CurrentAgentContext,
    permissions: PermissionServiceDep,
    channel_id: UUID,
    data: ChannelUpdate,
) -> ChannelResponse:
    """Update channel settings."""
    result = await db.execute(select(ChannelTable).where(ChannelTable.id == channel_id))
    channel = result.scalar_one_or_none()

    if not channel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Channel not found",
        )

    # Only Board, Main PM can update channels
    allowed_roles = {AgentRole.CEO, AgentRole.PRODUCT_OWNER, AgentRole.MAIN_PM}
    if agent.role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update channels",
        )

    # Update fields
    update_data = data.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(channel, field, value)

    await db.flush()

    return ChannelResponse(
        id=require_uuid(channel.id),
        name=channel.name,
        slug=channel.slug,
        type=channel.type,
        description=channel.description,
        topic=channel.topic,
        member_count=len(channel.members),
        message_count=channel.message_count,
        group_count=channel.group_count,
        is_archived=channel.is_archived,
        is_private=channel.is_private,
        can_write=permissions.can_write_channel(agent, channel.slug),
    )


@router.post(
    "/{channel_id}/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Add member",
    description="Add a member to the channel.",
)
async def add_member(
    db: DbSession,
    agent: CurrentAgentContext,
    channel_id: UUID,
    member_id: UUID,
    can_write: bool = Query(True),
) -> None:
    """Add a member to the channel."""
    # Only Board, Main PM can manage channel members
    allowed_roles = {AgentRole.CEO, AgentRole.PRODUCT_OWNER, AgentRole.MAIN_PM}
    if agent.role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to manage channel members",
        )

    result = await db.execute(select(ChannelTable).where(ChannelTable.id == channel_id))
    channel = result.scalar_one_or_none()

    if not channel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Channel not found",
        )

    # Add to members if not already present
    if member_id not in channel.members:
        channel.members = [*channel.members, member_id]

    # Add to writers if requested
    if can_write and member_id not in channel.writers:
        channel.writers = [*channel.writers, member_id]

    await db.flush()


@router.delete(
    "/{channel_id}/members/{member_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Remove member",
    description="Remove a member from the channel.",
)
async def remove_member(
    db: DbSession,
    agent: CurrentAgentContext,
    channel_id: UUID,
    member_id: UUID,
) -> None:
    """Remove a member from the channel."""
    # Only Board, Main PM can manage channel members
    allowed_roles = {AgentRole.CEO, AgentRole.PRODUCT_OWNER, AgentRole.MAIN_PM}
    if agent.role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to manage channel members",
        )

    result = await db.execute(select(ChannelTable).where(ChannelTable.id == channel_id))
    channel = result.scalar_one_or_none()

    if not channel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Channel not found",
        )

    # Remove from members and writers
    channel.members = [m for m in channel.members if m != member_id]
    channel.writers = [w for w in channel.writers if w != member_id]

    await db.flush()
