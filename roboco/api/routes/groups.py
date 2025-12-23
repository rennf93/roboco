"""
Groups API Routes

Endpoints for managing groups within channels.
Groups are created by Main PM to organize work into feature/initiative scopes.
Cell PMs then create sessions within groups for actual work items.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from roboco.api.deps import CurrentAgentContext, DbSession
from roboco.api.schemas.groups import (
    GroupCreateRequest,
    GroupDetailResponse,
    GroupResponse,
)
from roboco.models.base import AgentRole
from roboco.models.messaging import GroupCreateRequest as ServiceGroupCreate
from roboco.services.messaging import get_messaging_service
from roboco.utils.converters import require_uuid, to_python_uuid

router = APIRouter()

# Roles authorized to create groups
GROUP_ADMIN_ROLES = frozenset({AgentRole.CEO, AgentRole.MAIN_PM, AgentRole.AUDITOR})


@router.post(
    "",
    response_model=GroupResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create group",
    description=(
        "Create a new group in a channel. Groups organize work into "
        "feature/initiative scopes. Only Main PM can create groups."
    ),
)
async def create_group(
    db: DbSession,
    agent: CurrentAgentContext,
    data: GroupCreateRequest,
) -> GroupResponse:
    """
    Create a new group in a channel.

    Groups are the organizational unit between channels and sessions:
    - Main PM creates Groups for features/initiatives
    - Cell PMs create Sessions within Groups for work items
    - Developers communicate within Sessions
    """
    if agent.role not in GROUP_ADMIN_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Only Main PM, CEO, or Auditor can create groups. "
                "If you need a group created, escalate to Main PM."
            ),
        )

    service = get_messaging_service(db)

    # Get channel by slug
    channel = await service.get_channel_by_slug(data.channel_slug)
    if not channel:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Channel not found: {data.channel_slug}",
        )

    # Create group via service
    try:
        group = await service.create_group(
            ServiceGroupCreate(
                name=data.name,
                channel_id=require_uuid(channel.id),
                allowed_roles=data.allowed_roles,
                hierarchy_level=data.hierarchy_level,
            )
        )
        await db.commit()
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e

    return GroupResponse(
        id=require_uuid(group.id),
        name=group.name,
        channel_id=require_uuid(group.channel_id),
        channel_slug=data.channel_slug,
        hierarchy_level=group.hierarchy_level,
        is_active=group.is_active,
        total_sessions=group.total_sessions,
        total_messages=group.total_messages,
        active_session_id=to_python_uuid(group.active_session_id),
    )


@router.get(
    "/{group_id}",
    response_model=GroupDetailResponse,
    summary="Get group",
    description="Get detailed information about a group.",
)
async def get_group(
    db: DbSession,
    group_id: UUID,
) -> GroupDetailResponse:
    """Get a group by ID."""
    service = get_messaging_service(db)

    group = await service.get_group(group_id)
    if not group:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Group not found",
        )

    # Get channel slug for response
    channel = await service.get_channel(require_uuid(group.channel_id))
    channel_slug = channel.slug if channel else "unknown"

    return GroupDetailResponse(
        id=require_uuid(group.id),
        name=group.name,
        channel_id=require_uuid(group.channel_id),
        channel_slug=channel_slug,
        hierarchy_level=group.hierarchy_level,
        is_active=group.is_active,
        total_sessions=group.total_sessions,
        total_messages=group.total_messages,
        active_session_id=to_python_uuid(group.active_session_id),
        allowed_roles=[
            r.value if hasattr(r, "value") else r for r in group.allowed_roles
        ],
        members=list(group.members) if group.members else [],
    )
