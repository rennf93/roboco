"""
Permission Service

Implements the access control model:
- Task permissions by role
- Notification permissions (who can notify whom)
- Communication matrix (who can communicate with whom)

Permission Levels:
- L0: CEO (full access)
- L1: Board (cross-org access)
- L2: Main PM (all cells access)
- L3: Cell PM (own cell + PM coordination)
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

from roboco.foundation.identity import Role as _FoundationRole
from roboco.foundation.policy.communications import NOTIFY_SENDER_ROLES
from roboco.models import AgentRole, Team
from roboco.models.permissions import (
    KB_PERMISSIONS,
    ROLE_LEVELS,
    TASK_PERMISSIONS,
    AgentContext,
    PermissionLevel,
    TaskAction,
)
from roboco.services.base import SingletonService

# =============================================================================
# NOTIFICATION PERMISSIONS (derived from foundation.NOTIFY_SENDER_ROLES)
# =============================================================================
#
# Foundation owns the sender allowlist. Scope semantics (who each sender may
# reach) live here because they depend on the recipient's role + team — not
# pure identity data. The mapping below preserves the legacy
# NOTIFICATION_PERMISSIONS behaviour:
#   - main_pm / ceo            -> "all"   (no recipient filter)
#   - cell_pm                  -> "cell"  (own team members + any PM)
#   - product_owner            -> list    (management chain only)
#   - head_marketing           -> list    (management chain only)
# Auditor is intentionally NOT a sender (silent observer per spec §5.5).

# Board members notify the management chain only. Roles, not slugs — matched
# against recipient.role in can_notify(). Each list is the set of recipient
# roles the sender may notify.
_BOARD_NOTIFY_TARGETS: dict[AgentRole, frozenset[AgentRole]] = {
    AgentRole.PRODUCT_OWNER: frozenset(
        {AgentRole.MAIN_PM, AgentRole.HEAD_MARKETING, AgentRole.AUDITOR, AgentRole.CEO}
    ),
    AgentRole.HEAD_MARKETING: frozenset(
        {AgentRole.MAIN_PM, AgentRole.PRODUCT_OWNER, AgentRole.AUDITOR, AgentRole.CEO}
    ),
}


def _can_role_send_notifications(role: AgentRole) -> bool:
    """Whether a role may call notify(). Canonical in foundation."""
    try:
        return _FoundationRole(role.value) in NOTIFY_SENDER_ROLES
    except ValueError:
        return False


def _get_notification_scope(role: AgentRole) -> str | list[AgentRole]:
    """Scope of recipients a sender role may notify.

    Returns:
      - ``"all"`` for main_pm / ceo (no recipient filter)
      - ``"cell"`` for cell_pm (own team + any PM)
      - ``list[AgentRole]`` for board members (management chain only)
      - ``[]`` for roles that cannot send notifications
    """
    if role in (AgentRole.MAIN_PM, AgentRole.CEO):
        return "all"
    if role is AgentRole.CELL_PM:
        return "cell"
    targets = _BOARD_NOTIFY_TARGETS.get(role)
    if targets is not None:
        return list(targets)
    return []


# =============================================================================
# PERMISSION SERVICE
# =============================================================================


class PermissionService(SingletonService):
    """
    Service for checking and enforcing permissions.

    Implements the access control model.
    Uses agents_config.py as the SINGLE SOURCE OF TRUTH.

    Usage:
        service = PermissionService()

        # Check notification permission
        if service.can_notify(sender_ctx, recipient_ctx):
            await send_notification(...)
    """

    service_name: ClassVar[str] = "permissions"

    # =========================================================================
    # NOTIFICATION PERMISSIONS (foundation.NOTIFY_SENDER_ROLES + local scope)
    # =========================================================================

    def can_send_notifications(self, agent: AgentContext) -> bool:
        """Check if agent can send notifications at all."""
        return _can_role_send_notifications(agent.role)

    def can_notify(
        self,
        sender: AgentContext,
        recipient: AgentContext,
    ) -> bool:
        """Check if sender can notify recipient.

        Sender allowlist comes from foundation.NOTIFY_SENDER_ROLES.
        Scope rules are encoded in _get_notification_scope.
        """
        if not self.can_send_notifications(sender):
            return False

        scope = _get_notification_scope(sender.role)

        # "all" scope means can notify anyone
        if scope == "all":
            return True

        # "cell" scope means can only notify own cell members
        if scope == "cell":
            # Cell PM can notify other PMs (Cell PMs or Main PM) for coordination
            if recipient.role in (AgentRole.CELL_PM, AgentRole.MAIN_PM):
                return True
            # Otherwise must be same team
            return sender.team == recipient.team

        # List scope - check if recipient.role is in the allowed role list
        if isinstance(scope, list):
            return recipient.role in scope

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
        # The CEO is the ultimate authority and may perform any task action on
        # any task — unblock, reassign, cancel, override status, etc. Every
        # route that gates a write through this helper therefore lets the CEO
        # through (the panel operates as the CEO).
        if agent.role == AgentRole.CEO:
            return True
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
            "can_send_notifications": self.can_send_notifications(agent),
            "task_actions": list(self.get_task_actions(agent)),
        }


# =============================================================================
# ASYNC DATABASE LOOKUPS
# =============================================================================

# Roles with full org-wide access (no team/scope restriction).
PRIVILEGED_ROLES = frozenset({AgentRole.CEO, AgentRole.AUDITOR, AgentRole.MAIN_PM})


async def has_privileged_access(db: "AsyncSession", agent_id: UUID) -> bool:
    """
    Check if agent has a privileged role (CEO, Auditor, Main PM).

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


# PM_ROLES is canonical in foundation.identity. Re-export for backwards
# compatibility; new consumers import from foundation directly.
from roboco.foundation.identity import PM_ROLES  # noqa: F401, E402

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
