"""
API Dependencies

Shared dependencies for FastAPI routes.
"""

from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.db.base import get_db
from roboco.models import AgentRole, Team
from roboco.services.permissions import AgentContext, PermissionService

# Type alias for database session dependency
DbSession = Annotated[AsyncSession, Depends(get_db)]

# Global service instances
_permission_service: PermissionService | None = None


def get_permission_service() -> PermissionService:
    """Get or create the permission service singleton."""
    global _permission_service
    if _permission_service is None:
        _permission_service = PermissionService()
    return _permission_service


PermissionServiceDep = Annotated[PermissionService, Depends(get_permission_service)]


async def get_current_agent_id(
    x_agent_id: Annotated[str | None, Header()] = None,
) -> UUID:
    """
    Get the current agent ID from request headers.

    In production, this would validate a JWT token and extract the agent ID.
    For now, we use a simple header-based approach for development.

    Args:
        x_agent_id: Agent ID from X-Agent-ID header

    Returns:
        UUID of the current agent

    Raises:
        HTTPException: If agent ID is missing or invalid
    """
    if not x_agent_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Agent-ID header",
        )

    try:
        return UUID(x_agent_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid agent ID format: {e}",
        ) from e


# Type alias for current agent dependency
CurrentAgentId = Annotated[UUID, Depends(get_current_agent_id)]


async def get_optional_agent_id(
    x_agent_id: Annotated[str | None, Header()] = None,
) -> UUID | None:
    """
    Get the current agent ID if provided.

    Unlike get_current_agent_id, this doesn't raise an error if missing.
    """
    if not x_agent_id:
        return None

    try:
        return UUID(x_agent_id)
    except ValueError:
        return None


OptionalAgentId = Annotated[UUID | None, Depends(get_optional_agent_id)]


async def get_agent_context(
    x_agent_id: Annotated[str | None, Header()] = None,
    x_agent_role: Annotated[str | None, Header()] = None,
    x_agent_team: Annotated[str | None, Header()] = None,
) -> AgentContext:
    """
    Get the current agent context from request headers.

    In production, this would be extracted from a JWT token.
    For development, we use headers.

    Required headers:
        X-Agent-ID: UUID of the agent
        X-Agent-Role: Role (e.g., 'developer', 'cell_pm')

    Optional headers:
        X-Agent-Team: Team (e.g., 'backend', 'frontend')
    """
    if not x_agent_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Agent-ID header",
        )

    if not x_agent_role:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Agent-Role header",
        )

    try:
        agent_id = UUID(x_agent_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid agent ID format: {e}",
        ) from e

    try:
        role = AgentRole(x_agent_role.lower())
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid agent role: {e}",
        ) from e

    team: Team | None = None
    if x_agent_team:
        try:
            team = Team(x_agent_team.lower())
        except ValueError:
            pass  # Team is optional

    return AgentContext(
        agent_id=agent_id,
        role=role,
        team=team,
    )


CurrentAgentContext = Annotated[AgentContext, Depends(get_agent_context)]


def require_channel_read(channel_name: str):
    """
    Dependency factory that requires read access to a channel.

    Usage:
        @router.get("/channels/{channel_id}/messages")
        async def get_messages(
            agent: CurrentAgentContext,
            _: Annotated[None, Depends(require_channel_read("backend-cell"))],
        ):
            ...
    """

    async def check_permission(
        agent: CurrentAgentContext,
        permissions: PermissionServiceDep,
    ) -> None:
        if not permissions.can_read_channel(agent, channel_name):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"No read access to channel: {channel_name}",
            )

    return check_permission


def require_channel_write(channel_name: str):
    """
    Dependency factory that requires write access to a channel.
    """

    async def check_permission(
        agent: CurrentAgentContext,
        permissions: PermissionServiceDep,
    ) -> None:
        if not permissions.can_write_channel(agent, channel_name):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"No write access to channel: {channel_name}",
            )

    return check_permission


def require_notification_permission():
    """
    Dependency that requires the agent can send notifications.
    """

    async def check_permission(
        agent: CurrentAgentContext,
        permissions: PermissionServiceDep,
    ) -> None:
        if not permissions.can_send_notifications(agent):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not authorized to send notifications",
            )

    return check_permission
