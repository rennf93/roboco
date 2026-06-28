"""
API Dependencies

Shared dependencies for FastAPI routes.
"""

from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING, Annotated, Any
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from roboco.agents_config import CEO_AGENT_ID, verify_agent_token
from roboco.api.schemas.optimal import PaginationParams
from roboco.db.base import get_db
from roboco.db.tables import AgentTable
from roboco.foundation.identity import BOARD_ROLES, DEV_ROLES, PM_ROLES, Role
from roboco.models import AgentRole, Team
from roboco.runtime import AgentOrchestrator
from roboco.services.a2a import A2AService
from roboco.services.audit import get_audit_service
from roboco.services.gateway.choreographer import Choreographer, ChoreographerDeps
from roboco.services.gateway.content_actions import ContentActions, ContentActionsDeps
from roboco.services.gateway.evidence_repo import EvidenceRepo
from roboco.services.git import GitService
from roboco.services.journal import JournalService
from roboco.services.messaging import MessagingService
from roboco.services.notification import NotificationService
from roboco.services.notification_delivery import NotificationDeliveryService
from roboco.services.permissions import AgentContext, PermissionService
from roboco.services.product import ProductService
from roboco.services.repositories import resolve_agent_identity, resolve_agent_uuid
from roboco.services.task import TaskService
from roboco.services.work_session import WorkSessionService
from roboco.services.workspace import WorkspaceService

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


def clear_orchestrator() -> None:
    """Clear the global orchestrator instance (test teardown / re-init)."""
    _ServiceHolder.orchestrator = None


def get_orchestrator() -> AgentOrchestrator:
    """Get the global orchestrator instance."""
    if _ServiceHolder.orchestrator is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Orchestrator not initialized",
        )
    return _ServiceHolder.orchestrator


def get_orchestrator_or_none() -> AgentOrchestrator | None:
    """The global orchestrator instance, or ``None`` if not set.

    Used by the lifespan shutdown path, which must stop the orchestrator
    BEFORE closing the DB (orchestrator.stop() drains fire-and-forget DB
    writes and finalizes agent state) but must not crash when the app is run
    without a bootstrap-set orchestrator (e.g. tests, ``skip_orchestrator``).
    """
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


async def get_current_agent_slug(
    x_agent_id: Annotated[str | None, Header()] = None,
) -> str:
    """
    Get the current agent slug from request headers.

    Unlike get_current_agent_id, this returns the slug directly without
    resolving to UUID. Useful for A2A where we work with agent slugs.

    Args:
        x_agent_id: Agent slug from X-Agent-ID header

    Returns:
        Agent slug string

    Raises:
        HTTPException: If agent ID header is missing
    """
    if not x_agent_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Agent-ID header",
        )
    return x_agent_id


# Type alias for agent slug dependency
CurrentAgentSlug = Annotated[str, Depends(get_current_agent_slug)]


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


def _auth_required() -> bool:
    """True when agent HMAC auth is mandatory (prod-ish) vs opt-in (dev)."""
    val = os.environ.get("ROBOCO_AGENT_AUTH_REQUIRED", "").strip().lower()
    return val in ("1", "true", "yes")


def _check_agent_auth_token(
    x_agent_id: str,
    x_agent_role: str,
    x_agent_team: str | None,
    x_agent_token: str | None,
) -> None:
    """Enforce HMAC token when required; reject invalid tokens even in dev."""
    # Token verification: stops an agent on the Docker network from
    # spoofing another agent's role by setting headers directly. When
    # ROBOCO_AGENT_AUTH_REQUIRED is true, every request must carry a
    # token matching HMAC(id:role:team, secret). In dev it's optional
    # (so the panel / curl-for-debugging keep working), but any token
    # that IS presented is still verified — you can't bypass by
    # supplying an invalid token.
    if _auth_required() and not x_agent_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Agent-Token header (auth required)",
        )
    if x_agent_token and not verify_agent_token(
        x_agent_token,
        x_agent_id,
        x_agent_role,
        x_agent_team or "",
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Invalid X-Agent-Token — signature mismatch. Header "
                "values do not match the token issued for this agent."
            ),
        )


def require_panel_token(
    x_agent_token: Annotated[str | None, Header(alias="X-Agent-Token")] = None,
) -> None:
    """Panel (CEO) HMAC gate for the live-chat bridges.

    The HTTP analog of the WS ``_require_panel_token``: the panel is the only
    caller of the live intake/secretary chat, nginx injects the CEO-signed
    ``X-Agent-Token`` on ``/api/`` in prod, and browser ``EventSource`` cannot
    set headers — so the gate is token-only (no ``X-Agent-ID``; the stream is
    session-keyed and the panel is the sole client). In dev
    (``ROBOCO_AGENT_AUTH_REQUIRED`` unset) a missing token is allowed; a
    presented-but-forged token is still rejected, matching
    ``_check_agent_auth_token`` and the WS gate.
    """
    if _auth_required() and not x_agent_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-Agent-Token header (auth required)",
        )
    if x_agent_token and not verify_agent_token(x_agent_token, CEO_AGENT_ID, "ceo", ""):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid X-Agent-Token — signature mismatch.",
        )


async def _resolve_agent_identity(
    db: DbSession, x_agent_id: str, x_agent_role: str
) -> tuple[UUID, str]:
    """Return (agent_id, slug), handling the special `system` role."""
    if x_agent_role.lower() == "system":
        try:
            return UUID(x_agent_id), "system"
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid system agent UUID: {x_agent_id}",
            ) from e
    identity = await resolve_agent_identity(db, x_agent_id)
    if identity is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Agent not found: {x_agent_id}",
        )
    return identity


async def _coerce_agent_role(
    db: DbSession, x_agent_role: str, agent_id: UUID, x_agent_id: str
) -> AgentRole:
    """Parse the role header; fall back to the DB role if it's a slug."""
    try:
        return AgentRole(x_agent_role.lower())
    except ValueError:
        # Panel/clients sometimes pass the agent slug (e.g. "main-pm") instead
        # of the role value ("main_pm"). If the header isn't a valid enum
        # value, fall back to the authoritative role on the agent row we
        # already resolved above.
        role_row = await db.execute(
            select(AgentTable.role).where(AgentTable.id == agent_id)
        )
        db_role = role_row.scalar_one_or_none()
        if db_role is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Invalid agent role '{x_agent_role}' and no role on "
                    f"record for agent {x_agent_id}"
                ),
            ) from None
        return db_role


def _coerce_agent_team(x_agent_team: str | None) -> Team | None:
    """Parse the team header; return None for empty/invalid values."""
    if not x_agent_team:
        return None
    with contextlib.suppress(ValueError):
        return Team(x_agent_team.lower())
    return None


async def get_agent_context(
    db: DbSession,
    x_agent_id: Annotated[str | None, Header()] = None,
    x_agent_role: Annotated[str | None, Header()] = None,
    x_agent_team: Annotated[str | None, Header()] = None,
    x_agent_token: Annotated[str | None, Header()] = None,
) -> AgentContext:
    """
    Get the current agent context from request headers.

    Headers:
        X-Agent-ID: UUID or slug of the agent (e.g., "be-dev-1")
        X-Agent-Role: Role (e.g., 'developer', 'cell_pm')
        X-Agent-Team: (optional) Team (e.g., 'backend', 'frontend')
        X-Agent-Token: (required when ROBOCO_AGENT_AUTH_REQUIRED=true)
            HMAC of "agent_id:role:team" signed with
            ROBOCO_AGENT_AUTH_SECRET. Orchestrator issues this at spawn.
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

    _check_agent_auth_token(x_agent_id, x_agent_role, x_agent_team, x_agent_token)

    agent_id, slug = await _resolve_agent_identity(db, x_agent_id, x_agent_role)
    role = await _coerce_agent_role(db, x_agent_role, agent_id, x_agent_id)
    team = _coerce_agent_team(x_agent_team)

    return AgentContext(
        agent_id=agent_id,
        role=role,
        team=team,
        slug=slug,
    )


CurrentAgentContext = Annotated[AgentContext, Depends(get_agent_context)]


# =============================================================================
# ROLE-GATE HELPERS
#
# Small HTTP-layer guards for routes that need a coarse "PM or above" /
# "developer or above" check. They raise HTTPException directly because
# the check IS the HTTP authorization decision — no service-side logic,
# no translation layer needed.
# =============================================================================

# Role-sets derive from foundation so renaming a role lives in one file.
# HEAD_MARKETING is intentionally excluded from every "above" set — the role is
# a marketing spokesperson, not a workflow approver. StrEnum membership means
# the sets compare equal against both Role.* and the lowercase header string.
_PM_OR_ABOVE_ROLES: frozenset[Role] = (
    PM_ROLES | (BOARD_ROLES - {Role.HEAD_MARKETING}) | {Role.CEO}
)
_DEVELOPER_OR_ABOVE_ROLES: frozenset[Role] = DEV_ROLES | _PM_OR_ABOVE_ROLES


def _role_value(role: Any) -> str:
    """AgentRole or str → plain string for set membership checks."""
    return role.value if hasattr(role, "value") else str(role)


def require_pm_or_above(role: Any, action: str) -> None:
    """Raise 403 unless caller is PM-or-above (cell_pm/main_pm/board/CEO)."""
    if _role_value(role) not in _PM_OR_ABOVE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Only PMs and management can {action}",
        )


def require_developer_or_above(role: Any, action: str) -> None:
    """Raise 403 unless caller is developer-or-above."""
    if _role_value(role) not in _DEVELOPER_OR_ABOVE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Only developers and above can {action}",
        )


_GLOBAL_CELL_ACCESS_ROLES: frozenset[Role] = (BOARD_ROLES - {Role.HEAD_MARKETING}) | {
    Role.MAIN_PM,
    Role.CEO,
}


def require_cell_access(agent: AgentContext, cell: Team, action: str) -> None:
    """Raise 403 unless caller can act in the given cell.

    Main PM, board, and CEO can act across all cells. Cell PMs and their
    members are restricted to their own cell.
    """
    if _role_value(agent.role) in _GLOBAL_CELL_ACCESS_ROLES:
        return
    if agent.team != cell:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Cannot {action} projects in {cell.value} cell",
        )


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


# =============================================================================
# GATEWAY / CHOREOGRAPHER DEPENDENCIES
# =============================================================================


async def get_choreographer(
    db_session: DbSession,
) -> Choreographer:
    """Build a Choreographer with all service dependencies wired up."""
    from roboco.events.stream_bus import get_stream_event_bus

    # Inject the orchestrator (if initialised) and the stream event bus
    # so the rate-limited i_am_blocked path can park agents and publish events.
    # Both are None-safe in ChoreographerDeps — passing None is the same as
    # omitting the field, so the choreographer degrades gracefully when the
    # orchestrator has not been initialised yet (e.g. during startup).
    orch: AgentOrchestrator | None = _ServiceHolder.orchestrator
    bus = get_stream_event_bus() if _ServiceHolder.orchestrator is not None else None
    return Choreographer(
        ChoreographerDeps(
            task=TaskService(db_session),
            work_session=WorkSessionService(db_session),
            git=GitService(db_session),
            a2a=A2AService(db_session),
            journal=JournalService(db_session),
            audit=get_audit_service(),
            evidence_repo=EvidenceRepo(db_session),
            messaging=MessagingService(db_session),
            product=ProductService(db_session),
            orchestrator=orch,
            stream_bus=bus,
        )
    )


async def get_content_actions(
    db_session: DbSession,
) -> ContentActions:
    """Build a ContentActions with all service dependencies wired up."""
    return ContentActions(
        ContentActionsDeps(
            task=TaskService(db_session),
            git=GitService(db_session),
            messaging=MessagingService(db_session),
            a2a=A2AService(db_session),
            journal=JournalService(db_session),
            workspace=WorkspaceService(db_session),
            notifications=NotificationService(),
            notification_delivery=NotificationDeliveryService(db_session),
            evidence_repo=EvidenceRepo(db_session),
        )
    )


# =============================================================================
# PAGINATION DEPENDENCIES
# =============================================================================


def get_pagination(
    limit: int = 50,
    offset: int = 0,
) -> PaginationParams:
    """Dependency for pagination parameters."""
    # Enforce constraints
    limit = max(1, min(100, limit))
    offset = max(0, offset)
    return PaginationParams(limit=limit, offset=offset)


PaginationDep = Annotated[PaginationParams, Depends(get_pagination)]
