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

Note: This service uses enum-based roles (AgentRole) for type safety.
For string-based agent ID lookups, see roboco.agents_config.
"""

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any
from uuid import UUID

import structlog

from roboco.agents_config import (
    CHANNEL_ACCESS as CHANNEL_ACCESS_BY_ID,
)
from roboco.agents_config import (
    NOTIFICATION_PERMISSIONS as NOTIFICATION_PERMS_BY_ROLE,
)
from roboco.agents_config import (
    get_agent_role as get_role_string,
)
from roboco.models import AgentRole, ChannelType, Team

logger = structlog.get_logger()


# =============================================================================
# PERMISSION LEVELS
# =============================================================================


class PermissionLevel(IntEnum):
    """Hierarchical permission levels."""

    CEO = 0  # Full access
    BOARD = 1  # Cross-org access
    MAIN_PM = 2  # All cells access
    CELL_PM = 3  # Own cell + PM channel
    CELL_MEMBER = 4  # Own cell only
    AUDITOR = 99  # Special: silent read all


# Role to permission level mapping
ROLE_LEVELS: dict[AgentRole, PermissionLevel] = {
    AgentRole.CEO: PermissionLevel.CEO,
    AgentRole.PRODUCT_OWNER: PermissionLevel.BOARD,
    AgentRole.HEAD_MARKETING: PermissionLevel.BOARD,
    AgentRole.AUDITOR: PermissionLevel.AUDITOR,
    AgentRole.MAIN_PM: PermissionLevel.MAIN_PM,
    AgentRole.CELL_PM: PermissionLevel.CELL_PM,
    AgentRole.DEVELOPER: PermissionLevel.CELL_MEMBER,
    AgentRole.QA: PermissionLevel.CELL_MEMBER,
    AgentRole.DOCUMENTER: PermissionLevel.CELL_MEMBER,
}


# =============================================================================
# CHANNEL PERMISSIONS
# =============================================================================


@dataclass
class ChannelPermission:
    """Defines who can read/write to a channel."""

    channel_name: str
    channel_type: ChannelType

    # Roles that can read
    read_roles: set[AgentRole]

    # Roles that can write
    write_roles: set[AgentRole]

    # Teams that have access (for cell channels)
    teams: set[Team] = field(default_factory=set)

    # Whether Auditor has silent read access
    auditor_access: bool = True


# Default channel permissions per HOMELAB_TEAM_V0.md Section 12.2
DEFAULT_CHANNEL_PERMISSIONS: dict[str, ChannelPermission] = {
    # Cell channels - internal team
    "backend-cell": ChannelPermission(
        channel_name="backend-cell",
        channel_type=ChannelType.CELL,
        read_roles={
            AgentRole.DEVELOPER,
            AgentRole.QA,
            AgentRole.CELL_PM,
            AgentRole.DOCUMENTER,
        },
        write_roles={
            AgentRole.DEVELOPER,
            AgentRole.QA,
            AgentRole.CELL_PM,
            AgentRole.DOCUMENTER,
        },
        teams={Team.BACKEND},
    ),
    "frontend-cell": ChannelPermission(
        channel_name="frontend-cell",
        channel_type=ChannelType.CELL,
        read_roles={
            AgentRole.DEVELOPER,
            AgentRole.QA,
            AgentRole.CELL_PM,
            AgentRole.DOCUMENTER,
        },
        write_roles={
            AgentRole.DEVELOPER,
            AgentRole.QA,
            AgentRole.CELL_PM,
            AgentRole.DOCUMENTER,
        },
        teams={Team.FRONTEND},
    ),
    "uxui-cell": ChannelPermission(
        channel_name="uxui-cell",
        channel_type=ChannelType.CELL,
        read_roles={
            AgentRole.DEVELOPER,
            AgentRole.QA,
            AgentRole.CELL_PM,
            AgentRole.DOCUMENTER,
        },
        write_roles={
            AgentRole.DEVELOPER,
            AgentRole.QA,
            AgentRole.CELL_PM,
            AgentRole.DOCUMENTER,
        },
        teams={Team.UX_UI},
    ),
    # Cross-cell coordination
    "dev-all": ChannelPermission(
        channel_name="dev-all",
        channel_type=ChannelType.CROSS_CELL,
        read_roles={AgentRole.DEVELOPER, AgentRole.MAIN_PM},
        write_roles={AgentRole.DEVELOPER},
    ),
    "qa-all": ChannelPermission(
        channel_name="qa-all",
        channel_type=ChannelType.CROSS_CELL,
        read_roles={AgentRole.QA, AgentRole.MAIN_PM},
        write_roles={AgentRole.QA},
    ),
    "pm-all": ChannelPermission(
        channel_name="pm-all",
        channel_type=ChannelType.CROSS_CELL,
        read_roles={AgentRole.CELL_PM, AgentRole.MAIN_PM},
        write_roles={AgentRole.CELL_PM, AgentRole.MAIN_PM},
    ),
    "doc-all": ChannelPermission(
        channel_name="doc-all",
        channel_type=ChannelType.CROSS_CELL,
        read_roles={AgentRole.DOCUMENTER, AgentRole.MAIN_PM},
        write_roles={AgentRole.DOCUMENTER},
    ),
    # Management channels
    "main-pm-board": ChannelPermission(
        channel_name="main-pm-board",
        channel_type=ChannelType.MANAGEMENT,
        read_roles={
            AgentRole.MAIN_PM,
            AgentRole.PRODUCT_OWNER,
            AgentRole.HEAD_MARKETING,
            AgentRole.AUDITOR,
        },
        write_roles={
            AgentRole.MAIN_PM,
            AgentRole.PRODUCT_OWNER,
            AgentRole.HEAD_MARKETING,
        },
    ),
    "board-private": ChannelPermission(
        channel_name="board-private",
        channel_type=ChannelType.MANAGEMENT,
        read_roles={
            AgentRole.PRODUCT_OWNER,
            AgentRole.HEAD_MARKETING,
            AgentRole.AUDITOR,
            AgentRole.CEO,
        },
        write_roles={
            AgentRole.PRODUCT_OWNER,
            AgentRole.HEAD_MARKETING,
            AgentRole.CEO,
        },
    ),
    # Special channels
    "announcements": ChannelPermission(
        channel_name="announcements",
        channel_type=ChannelType.SPECIAL,
        read_roles=set(AgentRole),  # Everyone can read
        write_roles={
            AgentRole.PRODUCT_OWNER,
            AgentRole.HEAD_MARKETING,
            AgentRole.MAIN_PM,
            AgentRole.CEO,
        },
    ),
    "all-hands": ChannelPermission(
        channel_name="all-hands",
        channel_type=ChannelType.SPECIAL,
        read_roles=set(AgentRole),  # Everyone
        write_roles=set(AgentRole),  # Everyone can write
    ),
}


# =============================================================================
# NOTIFICATION PERMISSIONS
# =============================================================================


# Who can send notifications per HOMELAB_TEAM_V0.md Section 12.4
NOTIFICATION_SENDERS: set[AgentRole] = {
    AgentRole.CELL_PM,
    AgentRole.MAIN_PM,
    AgentRole.PRODUCT_OWNER,
    AgentRole.HEAD_MARKETING,
    AgentRole.AUDITOR,
    AgentRole.CEO,
}

# Who each role can notify
NOTIFICATION_TARGETS: dict[AgentRole, set[AgentRole]] = {
    # Cell PM can notify their own cell members
    AgentRole.CELL_PM: {
        AgentRole.DEVELOPER,
        AgentRole.QA,
        AgentRole.DOCUMENTER,
        AgentRole.CELL_PM,  # Other cell PMs for coordination
    },
    # Main PM can notify all PMs and escalate to any cell
    AgentRole.MAIN_PM: {
        AgentRole.CELL_PM,
        AgentRole.DEVELOPER,
        AgentRole.QA,
        AgentRole.DOCUMENTER,
    },
    # Product Owner can notify Main PM and Board
    AgentRole.PRODUCT_OWNER: {
        AgentRole.MAIN_PM,
        AgentRole.HEAD_MARKETING,
        AgentRole.AUDITOR,
    },
    # Head of Marketing can notify Main PM and Board
    AgentRole.HEAD_MARKETING: {
        AgentRole.MAIN_PM,
        AgentRole.PRODUCT_OWNER,
        AgentRole.AUDITOR,
    },
    # Auditor can notify anyone (special privilege)
    AgentRole.AUDITOR: set(AgentRole),
    # CEO can notify anyone
    AgentRole.CEO: set(AgentRole),
}


# =============================================================================
# COMMUNICATION MATRIX
# =============================================================================

# Per HOMELAB_TEAM_V0.md Section 3.5
# Defines who can directly communicate with whom

COMMUNICATION_MATRIX: dict[AgentRole, set[AgentRole]] = {
    # CEO can communicate with everyone
    AgentRole.CEO: set(AgentRole),
    # Board members
    AgentRole.PRODUCT_OWNER: {
        AgentRole.CEO,
        AgentRole.HEAD_MARKETING,
        AgentRole.AUDITOR,
        AgentRole.MAIN_PM,
    },
    AgentRole.HEAD_MARKETING: {
        AgentRole.CEO,
        AgentRole.PRODUCT_OWNER,
        AgentRole.AUDITOR,
        AgentRole.MAIN_PM,
    },
    # Auditor can communicate with everyone
    AgentRole.AUDITOR: set(AgentRole),
    # Main PM
    AgentRole.MAIN_PM: {
        AgentRole.CEO,
        AgentRole.PRODUCT_OWNER,
        AgentRole.HEAD_MARKETING,
        AgentRole.AUDITOR,
        AgentRole.CELL_PM,
    },
    # Cell PM communicates with their cell and other PMs
    AgentRole.CELL_PM: {
        AgentRole.CEO,
        AgentRole.AUDITOR,
        AgentRole.MAIN_PM,
        AgentRole.CELL_PM,
        AgentRole.DEVELOPER,
        AgentRole.QA,
        AgentRole.DOCUMENTER,
    },
    # Cell members communicate within cell
    AgentRole.DEVELOPER: {
        AgentRole.CEO,
        AgentRole.AUDITOR,
        AgentRole.CELL_PM,
        AgentRole.DEVELOPER,
        AgentRole.QA,
        AgentRole.DOCUMENTER,
    },
    AgentRole.QA: {
        AgentRole.CEO,
        AgentRole.AUDITOR,
        AgentRole.CELL_PM,
        AgentRole.DEVELOPER,
        AgentRole.QA,
        AgentRole.DOCUMENTER,
    },
    AgentRole.DOCUMENTER: {
        AgentRole.CEO,
        AgentRole.AUDITOR,
        AgentRole.CELL_PM,
        AgentRole.DEVELOPER,
        AgentRole.QA,
        AgentRole.DOCUMENTER,
    },
}


# =============================================================================
# TASK PERMISSIONS
# =============================================================================


class TaskAction:
    """Task actions that require permission."""

    VIEW_ALL = "view_all"
    VIEW_OWN = "view_own"
    CREATE = "create"
    ASSIGN = "assign"
    CLAIM = "claim"
    UPDATE_OWN = "update_own"
    CLOSE = "close"
    CHANGE_PRIORITY = "change_priority"


# Per HOMELAB_TEAM_V0.md Section 12.3
TASK_PERMISSIONS: dict[AgentRole, set[str]] = {
    AgentRole.CEO: {
        TaskAction.VIEW_ALL,
        TaskAction.CREATE,
        TaskAction.ASSIGN,
        TaskAction.CLOSE,
        TaskAction.CHANGE_PRIORITY,
    },
    AgentRole.PRODUCT_OWNER: {
        TaskAction.VIEW_ALL,
        TaskAction.CREATE,
        TaskAction.ASSIGN,
        TaskAction.CLOSE,
        TaskAction.CHANGE_PRIORITY,
    },
    AgentRole.HEAD_MARKETING: {
        TaskAction.VIEW_ALL,
        TaskAction.CREATE,
        TaskAction.ASSIGN,
        TaskAction.CLOSE,
        TaskAction.CHANGE_PRIORITY,
    },
    AgentRole.AUDITOR: {
        TaskAction.VIEW_ALL,
        TaskAction.CREATE,
        TaskAction.ASSIGN,
        TaskAction.CLOSE,
        TaskAction.CHANGE_PRIORITY,
    },
    AgentRole.MAIN_PM: {
        TaskAction.VIEW_ALL,
        TaskAction.CREATE,
        TaskAction.ASSIGN,
        TaskAction.CLOSE,
        TaskAction.CHANGE_PRIORITY,
    },
    AgentRole.CELL_PM: {
        TaskAction.VIEW_OWN,  # Own cell only
        TaskAction.CREATE,
        TaskAction.ASSIGN,
        TaskAction.CLOSE,
        TaskAction.CHANGE_PRIORITY,
    },
    AgentRole.DEVELOPER: {
        TaskAction.VIEW_OWN,
        TaskAction.CLAIM,
        TaskAction.UPDATE_OWN,
        TaskAction.CLOSE,  # Can close own tasks
    },
    AgentRole.QA: {
        TaskAction.VIEW_OWN,
        TaskAction.CLAIM,
        TaskAction.UPDATE_OWN,
    },
    AgentRole.DOCUMENTER: {
        TaskAction.VIEW_OWN,
        TaskAction.CLAIM,
        TaskAction.UPDATE_OWN,
    },
}


# =============================================================================
# PERMISSION SERVICE
# =============================================================================


@dataclass
class AgentContext:
    """Context for permission checks."""

    agent_id: UUID
    role: AgentRole
    team: Team | None = None

    @property
    def level(self) -> PermissionLevel:
        return ROLE_LEVELS.get(self.role, PermissionLevel.CELL_MEMBER)


class PermissionService:
    """
    Service for checking and enforcing permissions.

    Implements the access control model from HOMELAB_TEAM_V0.md.

    Usage:
        service = PermissionService()

        # Check channel access
        if service.can_read_channel(agent_ctx, channel_name):
            messages = await get_messages(channel_name)

        # Check notification permission
        if service.can_notify(sender_ctx, recipient_ctx):
            await send_notification(...)
    """

    def __init__(self) -> None:
        self.log = logger.bind(component="permissions")

        # Channel permissions (can be customized)
        self._channel_permissions = DEFAULT_CHANNEL_PERMISSIONS.copy()

    # =========================================================================
    # CHANNEL PERMISSIONS
    # =========================================================================

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

        permission = self._channel_permissions.get(channel_name)
        if not permission:
            self.log.warning("Unknown channel", channel=channel_name)
            return False

        # Check role-based access
        if agent.role in permission.read_roles:
            # For cell channels, also check team membership
            if permission.channel_type == ChannelType.CELL:
                if permission.teams and agent.team not in permission.teams:
                    return False
            return True

        # Higher permission levels can read lower-level channels
        return agent.level <= PermissionLevel.MAIN_PM

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
        # They CAN notify anyone though
        if agent.role == AgentRole.AUDITOR:
            return True

        permission = self._channel_permissions.get(channel_name)
        if not permission:
            self.log.warning("Unknown channel", channel=channel_name)
            return False

        # Check role-based access
        if agent.role in permission.write_roles:
            # For cell channels, also check team membership
            if permission.channel_type == ChannelType.CELL:
                if permission.teams and agent.team not in permission.teams:
                    return False
            return True

        # Higher permission levels can write to lower-level channels
        return agent.level <= PermissionLevel.MAIN_PM

    def get_accessible_channels(
        self,
        agent: AgentContext,
    ) -> list[str]:
        """Get list of channels an agent can read."""
        channels = []
        for channel_name in self._channel_permissions:
            if self.can_read_channel(agent, channel_name):
                channels.append(channel_name)
        return channels

    def get_writable_channels(
        self,
        agent: AgentContext,
    ) -> list[str]:
        """Get list of channels an agent can write to."""
        channels = []
        for channel_name in self._channel_permissions:
            if self.can_write_channel(agent, channel_name):
                channels.append(channel_name)
        return channels

    # =========================================================================
    # NOTIFICATION PERMISSIONS
    # =========================================================================

    def can_send_notifications(self, agent: AgentContext) -> bool:
        """Check if agent can send notifications at all."""
        return agent.role in NOTIFICATION_SENDERS

    def can_notify(
        self,
        sender: AgentContext,
        recipient: AgentContext,
    ) -> bool:
        """Check if sender can notify recipient."""
        if not self.can_send_notifications(sender):
            return False

        allowed_targets = NOTIFICATION_TARGETS.get(sender.role, set())

        # Check if recipient role is in allowed targets
        if recipient.role in allowed_targets:
            # For Cell PM, also check team membership
            if sender.role == AgentRole.CELL_PM:
                # Cell PM can only notify their own cell (unless coordinating with other PMs)
                if recipient.role != AgentRole.CELL_PM:
                    if sender.team != recipient.team:
                        return False
            return True

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
            if sender.level >= PermissionLevel.CELL_MEMBER:
                if recipient.level >= PermissionLevel.CELL_MEMBER:
                    # Cell members can only communicate within their cell
                    # unless going through PM
                    if sender.team != recipient.team:
                        # Exception: going through shared channels
                        return False
            return True

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
            if action == TaskAction.VIEW_OWN and task_team:
                if agent.team and agent.team != task_team:
                    return False
            return True

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
    # UTILITY
    # =========================================================================

    def register_channel(
        self,
        permission: ChannelPermission,
    ) -> None:
        """Register a custom channel permission."""
        self._channel_permissions[permission.channel_name] = permission
        self.log.info(
            "Registered channel",
            channel=permission.channel_name,
            type=permission.channel_type.value,
        )

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
    # STRING-BASED LOOKUPS (bridges to agents_config)
    # =========================================================================

    def can_agent_read_channel(self, agent_slug: str, channel_slug: str) -> bool:
        """
        Check channel access using agent slug (string ID).

        This bridges to the agents_config module for string-based lookups.
        """
        channel = CHANNEL_ACCESS_BY_ID.get(channel_slug)
        if not channel:
            return False

        read_list = channel.get("read", [])
        silent_list = channel.get("silent", [])

        return agent_slug in read_list or agent_slug in silent_list

    def can_agent_write_channel(self, agent_slug: str, channel_slug: str) -> bool:
        """
        Check channel write access using agent slug (string ID).

        This bridges to the agents_config module for string-based lookups.
        """
        channel = CHANNEL_ACCESS_BY_ID.get(channel_slug)
        if not channel:
            return False

        write_list = channel.get("write", [])
        return agent_slug in write_list

    def can_agent_send_notifications(self, agent_slug: str) -> bool:
        """
        Check notification permission using agent slug (string ID).

        This bridges to the agents_config module for string-based lookups.
        """
        role = get_role_string(agent_slug)
        perms = NOTIFICATION_PERMS_BY_ROLE.get(role, {})
        return perms.get("can_send", False)
