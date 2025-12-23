"""
API Dependencies

Shared dependencies for FastAPI routes.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Annotated, Any
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.db.base import get_db
from roboco.models import AgentRole, Team
from roboco.runtime import AgentOrchestrator
from roboco.services.permissions import AgentContext, PermissionService
from roboco.services.repositories import resolve_agent_identity, resolve_agent_uuid

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

# Type alias for database session dependency
DbSession = Annotated[AsyncSession, Depends(get_db)]


async def resolve_agent_id(agent_id_str: str, db: AsyncSession) -> UUID:
    """
    Resolve agent ID from string (UUID or slug).

    Args:
        agent_id_str: Either a UUID string or agent slug (e.g., "be-dev-1")
        db: Database session

    Returns:
        UUID of the agent

    Raises:
        HTTPException: If agent not found or invalid format
    """
    result = await resolve_agent_uuid(db, agent_id_str)

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Agent not found: {agent_id_str}",
        )

    return result


class _ServiceHolder:
    """Holder for singleton service instances."""

    permission_service: PermissionService | None = None
    orchestrator: AgentOrchestrator | None = None


def get_permission_service() -> PermissionService:
    """Get or create the permission service singleton."""
    if _ServiceHolder.permission_service is None:
        _ServiceHolder.permission_service = PermissionService()
    return _ServiceHolder.permission_service


PermissionServiceDep = Annotated[PermissionService, Depends(get_permission_service)]


def set_orchestrator(orchestrator: AgentOrchestrator) -> None:
    """Set the global orchestrator instance."""
    _ServiceHolder.orchestrator = orchestrator


def get_orchestrator() -> AgentOrchestrator:
    """Get the global orchestrator instance."""
    if _ServiceHolder.orchestrator is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Orchestrator not initialized",
        )
    return _ServiceHolder.orchestrator


OrchestratorDep = Annotated[AgentOrchestrator, Depends(get_orchestrator)]


async def get_current_agent_id(
    db: DbSession,
    x_agent_id: Annotated[str | None, Header()] = None,
) -> UUID:
    """
    Get the current agent ID from request headers.

    Accepts either a UUID string or agent slug (e.g., "be-dev-1").
    In production, this would validate a JWT token and extract the agent ID.
    For now, we use a simple header-based approach for development.

    Args:
        x_agent_id: Agent ID (UUID or slug) from X-Agent-ID header
        db: Database session for slug resolution

    Returns:
        UUID of the current agent

    Raises:
        HTTPException: If agent ID is missing or invalid/not found
    """
    if not x_agent_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Agent-ID header",
        )

    return await resolve_agent_id(x_agent_id, db)


# Type alias for current agent dependency
CurrentAgentId = Annotated[UUID, Depends(get_current_agent_id)]


async def get_optional_agent_id(
    db: DbSession,
    x_agent_id: Annotated[str | None, Header()] = None,
) -> UUID | None:
    """
    Get the current agent ID if provided.

    Accepts either a UUID string or agent slug (e.g., "be-dev-1").
    Unlike get_current_agent_id, this doesn't raise an error if missing.
    """
    if not x_agent_id:
        return None

    try:
        return await resolve_agent_id(x_agent_id, db)
    except HTTPException:
        return None


OptionalAgentId = Annotated[UUID | None, Depends(get_optional_agent_id)]


async def get_agent_context(
    db: DbSession,
    x_agent_id: Annotated[str | None, Header()] = None,
    x_agent_role: Annotated[str | None, Header()] = None,
    x_agent_team: Annotated[str | None, Header()] = None,
) -> AgentContext:
    """
    Get the current agent context from request headers.

    In production, this would be extracted from a JWT token.
    For development, we use headers.

    Required headers:
        X-Agent-ID: UUID or slug of the agent (e.g., "be-dev-1")
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

    # Special case: system role (orchestrator) uses well-known UUID
    # that doesn't exist in the database - bypass DB lookup
    if x_agent_role.lower() == "system":
        try:
            agent_id = UUID(x_agent_id)
            slug = "system"
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid system agent UUID: {x_agent_id}",
            ) from e
    else:
        # Resolve agent ID and slug from database
        identity = await resolve_agent_identity(db, x_agent_id)
        if identity is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Agent not found: {x_agent_id}",
            )
        agent_id, slug = identity

    try:
        role = AgentRole(x_agent_role.lower())
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid agent role: {e}",
        ) from e

    team: Team | None = None
    if x_agent_team:
        with contextlib.suppress(ValueError):
            team = Team(x_agent_team.lower())

    return AgentContext(
        agent_id=agent_id,
        role=role,
        team=team,
        slug=slug,
    )


CurrentAgentContext = Annotated[AgentContext, Depends(get_agent_context)]


def require_channel_read(
    channel_name: str,
) -> Callable[..., Coroutine[Any, Any, None]]:
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


def require_channel_write(
    channel_name: str,
) -> Callable[..., Coroutine[Any, Any, None]]:
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


def require_notification_permission() -> Callable[..., Coroutine[Any, Any, None]]:
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


def require_task_action(
    action: str, task_team: Team | None = None
) -> Callable[..., Coroutine[Any, Any, None]]:
    """
    Dependency factory that requires permission for a task action.

    Args:
        action: The task action (from TaskAction constants)
        task_team: Optional team context for team-specific checks

    Usage:
        @router.post("/tasks")
        async def create_task(
            agent: CurrentAgentContext,
            _: Annotated[None, Depends(require_task_action("create"))],
        ):
            ...
    """

    async def check_permission(
        agent: CurrentAgentContext,
        permissions: PermissionServiceDep,
    ) -> None:
        if not permissions.can_perform_task_action(agent, action, task_team):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Not authorized to perform task action: {action}",
            )

    return check_permission
