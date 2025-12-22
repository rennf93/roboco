"""
Permission Service

Implements the access control model from HOMELAB_TEAM_V0.md:
- Channel read/write permissions
- Task permissions by role
- Notification permissions (who can notify whom)
- Communication matrix (who can communicate with whom)

Permission Levels:
- L0: CEO (full access)
- L1: Board (cross-org access)
- L2: Main PM (all cells access)
- L3: Cell PM (own cell + PM channel)
- L4: Cell Members (own cell only)
- SPECIAL: Auditor (silent read all)

Architecture:
- agents_config.py is the SINGLE SOURCE OF TRUTH for permission configuration
- This service provides runtime enforcement using AgentContext (role + team)
- No duplicate permission definitions - all derived from agents_config
"""

from typing import TYPE_CHECKING, Any, ClassVar
from uuid import UUID

from sqlalchemy import select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from roboco.agents_config import (
    AGENT_ROLE_MAP,
    AGENT_TEAM_MAP,
    CHANNEL_ACCESS,
    NOTIFICATION_PERMISSIONS,
)
from roboco.agents_config import (
    get_agent_role as get_role_string,
)
from roboco.models import AgentRole, Team
from roboco.models.permissions import (
    COMMUNICATION_MATRIX,
    KB_PERMISSIONS,
    ROLE_LEVELS,
    TASK_PERMISSIONS,
    AgentContext,
    PermissionLevel,
    TaskAction,
)
from roboco.services.base import SingletonService

# =============================================================================
# CHANNEL PERMISSIONS (derived from agents_config.CHANNEL_ACCESS)
# =============================================================================

# Build role→team mapping from agents_config for efficient lookups
_ROLE_TEAM_LOOKUP: dict[tuple[str, str | None], list[str]] = {}
for agent_slug, role in AGENT_ROLE_MAP.items():
    team = AGENT_TEAM_MAP.get(agent_slug)
    key = (role, team)
    if key not in _ROLE_TEAM_LOOKUP:
        _ROLE_TEAM_LOOKUP[key] = []
    _ROLE_TEAM_LOOKUP[key].append(agent_slug)


def _get_agents_for_role_team(role: AgentRole, team: Team | None) -> list[str]:
    """Get all agent slugs that match a role and optional team."""
    role_str = role.value
    team_str = team.value if team else None
    return _ROLE_TEAM_LOOKUP.get((role_str, team_str), [])


# =============================================================================
# NOTIFICATION PERMISSIONS (derived from agents_config.NOTIFICATION_PERMISSIONS)
# =============================================================================


def _can_role_send_notifications(role: AgentRole) -> bool:
    """Check if a role can send notifications (from agents_config)."""
    perms = NOTIFICATION_PERMISSIONS.get(role.value, {})
    return bool(perms.get("can_send", False))


def _get_notification_scope(role: AgentRole) -> str | list[str]:
    """Get the notification scope for a role (from agents_config)."""
    perms = NOTIFICATION_PERMISSIONS.get(role.value, {})
    scope = perms.get("scope", [])
    return str(scope) if isinstance(scope, str) else list(scope) if scope else []


# =============================================================================
# PERMISSION SERVICE
# =============================================================================


class PermissionService(SingletonService):
    """
    Service for checking and enforcing permissions.

    Implements the access control model from HOMELAB_TEAM_V0.md.
    Uses agents_config.py as the SINGLE SOURCE OF TRUTH.

    Usage:
        service = PermissionService()

        # Check channel access
        if service.can_read_channel(agent_ctx, channel_name):
            messages = await get_messages(channel_name)

        # Check notification permission
        if service.can_notify(sender_ctx, recipient_ctx):
            await send_notification(...)
    """

    service_name: ClassVar[str] = "permissions"
    # No duplicate storage - uses agents_config.CHANNEL_ACCESS directly

    # =========================================================================
    # CHANNEL PERMISSIONS (uses agents_config.CHANNEL_ACCESS)
    # =========================================================================

    def _check_channel_access_for_agent(
        self,
        agent: AgentContext,
        channel_name: str,
        access_type: str,
    ) -> bool:
        """
        Check channel access using agents_config.CHANNEL_ACCESS.

        Converts AgentContext (role+team) to potential agent slugs,
        then checks if any of them have access.
        """
        channel = CHANNEL_ACCESS.get(channel_name)
        if not channel:
            self.log.warning("Unknown channel", channel=channel_name)
            return False

        # Get list of agent slugs that match this role+team
        agent_slugs = _get_agents_for_role_team(agent.role, agent.team)

        # Check if any matching agent has the requested access
        access_list = channel.get(access_type, [])
        silent_list = channel.get("silent", [])

        for slug in agent_slugs:
            if slug in access_list:
                return True
            if access_type == "read" and slug in silent_list:
                return True

        return False

    def can_read_channel(
        self,
        agent: AgentContext,
        channel_name: str,
    ) -> bool:
        """Check if agent can read from a channel."""
        # Auditor has silent read access to everything
        if agent.role == AgentRole.AUDITOR:
            return True

        # CEO has full access
        if agent.role == AgentRole.CEO:
            return True

        # Main PM has access to all channels
        if agent.role == AgentRole.MAIN_PM:
            return True

        return self._check_channel_access_for_agent(agent, channel_name, "read")

    def can_write_channel(
        self,
        agent: AgentContext,
        channel_name: str,
    ) -> bool:
        """Check if agent can write to a channel."""
        # CEO has full access
        if agent.role == AgentRole.CEO:
            return True

        # Auditor can write but usually doesn't (to maintain cover)
        if agent.role == AgentRole.AUDITOR:
            return True

        # Main PM has access to all channels
        if agent.role == AgentRole.MAIN_PM:
            return True

        return self._check_channel_access_for_agent(agent, channel_name, "write")

    def get_accessible_channels(
        self,
        agent: AgentContext,
    ) -> list[str]:
        """Get list of channels an agent can read."""
        channels = []
        for channel_name in CHANNEL_ACCESS:
            if self.can_read_channel(agent, channel_name):
                channels.append(channel_name)
        return channels

    def get_writable_channels(
        self,
        agent: AgentContext,
    ) -> list[str]:
        """Get list of channels an agent can write to."""
        channels = []
        for channel_name in CHANNEL_ACCESS:
            if self.can_write_channel(agent, channel_name):
                channels.append(channel_name)
        return channels

    # =========================================================================
    # NOTIFICATION PERMISSIONS (uses agents_config.NOTIFICATION_PERMISSIONS)
    # =========================================================================

    def can_send_notifications(self, agent: AgentContext) -> bool:
        """Check if agent can send notifications at all."""
        return _can_role_send_notifications(agent.role)

    def can_notify(
        self,
        sender: AgentContext,
        recipient: AgentContext,
    ) -> bool:
        """
        Check if sender can notify recipient.

        Uses agents_config.NOTIFICATION_PERMISSIONS for scope rules.
        """
        if not self.can_send_notifications(sender):
            return False

        scope = _get_notification_scope(sender.role)

        # "all" scope means can notify anyone
        if scope == "all":
            return True

        # "cell" scope means can only notify own cell members
        if scope == "cell":
            # Cell PM can only notify their own cell unless coordinating with PMs
            if recipient.role == AgentRole.CELL_PM:
                # PMs can notify other PMs for coordination
                return True
            # Otherwise must be same team
            return sender.team == recipient.team

        # List scope - check if recipient slug is in the allowed list
        if isinstance(scope, list):
            # Get recipient's potential slugs
            recipient_slugs = _get_agents_for_role_team(recipient.role, recipient.team)
            return any(slug in scope for slug in recipient_slugs)

        return False

    # =========================================================================
    # COMMUNICATION PERMISSIONS
    # =========================================================================

    def can_communicate(
        self,
        sender: AgentContext,
        recipient: AgentContext,
    ) -> bool:
        """
        Check if sender can directly communicate with recipient.

        This is for direct messages, not channel messages.
        Channel access is checked separately.
        """
        allowed = COMMUNICATION_MATRIX.get(sender.role, set())

        if recipient.role in allowed:
            # For cell members, check if same team
            # Cell members can only communicate within their cell
            sender_is_cell_member = sender.level >= PermissionLevel.CELL_MEMBER
            recipient_is_cell_member = recipient.level >= PermissionLevel.CELL_MEMBER
            different_teams = sender.team != recipient.team
            cross_cell = (
                sender_is_cell_member and recipient_is_cell_member and different_teams
            )
            return not cross_cell

        return False

    # =========================================================================
    # TASK PERMISSIONS
    # =========================================================================

    def can_perform_task_action(
        self,
        agent: AgentContext,
        action: str,
        task_team: Team | None = None,
    ) -> bool:
        """Check if agent can perform a task action."""
        allowed_actions = TASK_PERMISSIONS.get(agent.role, set())

        if action in allowed_actions:
            # VIEW_OWN means only own cell
            is_view_own = action == TaskAction.VIEW_OWN and task_team
            wrong_team = agent.team and agent.team != task_team
            return not (is_view_own and wrong_team)

        # Check VIEW_ALL permission for VIEW_OWN requests
        return bool(
            action == TaskAction.VIEW_OWN and TaskAction.VIEW_ALL in allowed_actions
        )

    def get_task_actions(
        self,
        agent: AgentContext,
    ) -> set[str]:
        """Get all task actions an agent can perform."""
        return TASK_PERMISSIONS.get(agent.role, set())

    # =========================================================================
    # KNOWLEDGE BASE PERMISSIONS
    # =========================================================================

    def can_perform_kb_action(
        self,
        agent: AgentContext,
        action: str,
    ) -> bool:
        """Check if agent can perform a knowledge base action."""
        allowed_actions = KB_PERMISSIONS.get(agent.role, set())
        return action in allowed_actions

    def get_kb_actions(
        self,
        agent: AgentContext,
    ) -> set[str]:
        """Get all KB actions an agent can perform."""
        return KB_PERMISSIONS.get(agent.role, set())

    # =========================================================================
    # UTILITY
    # =========================================================================

    def get_permission_level(self, role: AgentRole) -> PermissionLevel:
        """Get the permission level for a role."""
        return ROLE_LEVELS.get(role, PermissionLevel.CELL_MEMBER)

    def check_all(
        self,
        agent: AgentContext,
    ) -> dict[str, Any]:
        """Get comprehensive permission summary for an agent."""
        return {
            "agent_id": str(agent.agent_id),
            "role": agent.role.value,
            "team": agent.team.value if agent.team else None,
            "level": self.get_permission_level(agent.role).name,
            "readable_channels": self.get_accessible_channels(agent),
            "writable_channels": self.get_writable_channels(agent),
            "can_send_notifications": self.can_send_notifications(agent),
            "task_actions": list(self.get_task_actions(agent)),
        }

    # =========================================================================
    # STRING-BASED LOOKUPS (direct access to agents_config)
    # =========================================================================

    def can_agent_read_channel(self, agent_slug: str, channel_slug: str) -> bool:
        """
        Check channel access using agent slug (string ID).

        Direct lookup in agents_config.CHANNEL_ACCESS.
        """
        channel = CHANNEL_ACCESS.get(channel_slug)
        if not channel:
            return False

        read_list = channel.get("read", [])
        silent_list = channel.get("silent", [])

        return agent_slug in read_list or agent_slug in silent_list

    def can_agent_write_channel(self, agent_slug: str, channel_slug: str) -> bool:
        """
        Check channel write access using agent slug (string ID).

        Direct lookup in agents_config.CHANNEL_ACCESS.
        """
        channel = CHANNEL_ACCESS.get(channel_slug)
        if not channel:
            return False

        write_list = channel.get("write", [])
        return agent_slug in write_list

    def can_agent_send_notifications(self, agent_slug: str) -> bool:
        """
        Check notification permission using agent slug (string ID).

        Direct lookup in agents_config.NOTIFICATION_PERMISSIONS.
        """
        role = get_role_string(agent_slug)
        perms = NOTIFICATION_PERMISSIONS.get(role, {})
        return bool(perms.get("can_send", False))


# =============================================================================
# ASYNC DATABASE LOOKUPS
# =============================================================================

# Roles with full access to all channels (bypass membership checks)
PRIVILEGED_ROLES = frozenset({AgentRole.CEO, AgentRole.AUDITOR, AgentRole.MAIN_PM})


async def has_privileged_access(db: "AsyncSession", agent_id: UUID) -> bool:
    """
    Check if agent has a privileged role (CEO, Auditor, Main PM).

    These roles have full access to all channels regardless of membership.
    Queries by both id and slug since agent_id could be either
    (CEO uses UUID-style slug, others use short slugs like "be-dev-1").
    """
    from roboco.db.tables import AgentTable

    result = await db.execute(
        select(AgentTable.role).where(
            (AgentTable.id == agent_id) | (AgentTable.slug == str(agent_id))
        )
    )
    role = result.scalar_one_or_none()
    return role in PRIVILEGED_ROLES if role else False


# Roles that can manage tasks and sessions (PMs and board)
PM_ROLES = frozenset({AgentRole.CELL_PM, AgentRole.MAIN_PM})
MANAGEMENT_ROLES = frozenset(
    {AgentRole.CEO, AgentRole.PRODUCT_OWNER, AgentRole.CELL_PM, AgentRole.MAIN_PM}
)


async def is_pm_role(db: "AsyncSession", agent_id: UUID) -> bool:
    """
    Check if agent has a PM or management role.

    PM roles (Cell PM, Main PM) and management roles (CEO, Product Owner)
    can create task-linked sessions and assign work.
    """
    from roboco.db.tables import AgentTable

    result = await db.execute(
        select(AgentTable.role).where(
            (AgentTable.id == agent_id) | (AgentTable.slug == str(agent_id))
        )
    )
    role = result.scalar_one_or_none()
    return role in MANAGEMENT_ROLES if role else False
