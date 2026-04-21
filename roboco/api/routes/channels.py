"""
Channel Routes

CRUD for communication channels. Thin HTTP plumbing — all DB reads/writes
are delegated to `MessagingService`.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from roboco.api.deps import CurrentAgentContext, DbSession, PermissionServiceDep
from roboco.api.schemas.channels import (
    ChannelDetailResponse,
    ChannelListResponse,
    ChannelResponse,
    GroupResponse,
    ListChannelsQuery,
    require_channel_admin,
)
from roboco.models import AgentRole, ChannelCreate, ChannelUpdate
from roboco.models.messaging import ChannelCreateRequest
from roboco.services.base import NotFoundError
from roboco.services.messaging import get_messaging_service
from roboco.utils.converters import require_uuid, to_python_uuid

router = APIRouter()


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
    """List channels the agent can access, paginated."""
    accessible_slugs = permissions.get_accessible_channels(agent)

    if params.slug:
        if params.slug not in accessible_slugs:
            return ChannelListResponse(
                items=[],
                total=0,
                page=params.page,
                page_size=params.page_size,
            )
        accessible_slugs = [params.slug]

    messaging = get_messaging_service(db)
    channels, total = await messaging.list_channels_paginated(
        accessible_slugs=accessible_slugs,
        include_archived=params.include_archived,
        page=params.page,
        page_size=params.page_size,
    )

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
    """Get channel details + groups."""
    messaging = get_messaging_service(db)
    try:
        channel = await messaging.get_channel_with_groups_or_raise(channel_id)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

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
    messaging = get_messaging_service(db)
    try:
        channel = await messaging.get_channel_with_groups_or_raise(channel_id)
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

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
    allowed_roles = {AgentRole.CEO, AgentRole.PRODUCT_OWNER, AgentRole.MAIN_PM}
    if agent.role not in allowed_roles:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to create channels",
        )

    messaging = get_messaging_service(db)
    try:
        channel = await messaging.create_channel(
            ChannelCreateRequest(
                name=data.name,
                slug=data.slug,
                channel_type=data.type,
                description=data.description,
                members=list(data.members),
                writers=list(data.writers),
                silent_observers=list(data.silent_observers),
                is_private=data.is_private,
            )
        )
    except ValueError as e:
        # Service raises ValueError on duplicate slug — surface as 409.
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e

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
    """Update channel fields."""
    require_channel_admin(agent)
    messaging = get_messaging_service(db)
    try:
        channel = await messaging.update_channel_fields(
            channel_id=channel_id,
            fields=data.model_dump(exclude_unset=True),
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e

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
    require_channel_admin(agent)
    messaging = get_messaging_service(db)
    try:
        await messaging.add_channel_member_or_raise(
            channel_id=channel_id,
            member_id=member_id,
            can_write=can_write,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e


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
    require_channel_admin(agent)
    messaging = get_messaging_service(db)
    try:
        await messaging.remove_channel_member_or_raise(
            channel_id=channel_id,
            member_id=member_id,
        )
    except NotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
