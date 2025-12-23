"""
Permission Models

Data classes and enums for permission management.
"""

from dataclasses import dataclass
from enum import IntEnum
from uuid import UUID

from roboco.agents_config import ROLE_PERMISSION_LEVELS
from roboco.models import AgentRole, Team


class PermissionLevel(IntEnum):
    """Hierarchical permission levels."""

    CEO = 0  # Full access
    BOARD = 1  # Cross-org access
    MAIN_PM = 2  # All cells access
    CELL_PM = 3  # Own cell + PM channel
    CELL_MEMBER = 4  # Own cell only
    AUDITOR = 99  # Special: silent read all


# Build ROLE_LEVELS from agents_config (single source of truth)
def _build_role_levels() -> dict[AgentRole, PermissionLevel]:
    """Build role-to-level mapping from agents_config."""
    mapping: dict[AgentRole, PermissionLevel] = {}
    for role_str, level_str in ROLE_PERMISSION_LEVELS.items():
        try:
            role = AgentRole(role_str)
            level = PermissionLevel[level_str]
            mapping[role] = level
        except (ValueError, KeyError):
            pass
    return mapping


ROLE_LEVELS: dict[AgentRole, PermissionLevel] = _build_role_levels()


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


class KBAction:
    """Knowledge base actions that require permission."""

    INDEX_CODE = "index_code"
    INDEX_DOCS = "index_docs"
    SEARCH = "search"
    QUERY = "query"
    VIEW_STATS = "view_stats"
    CLEAR_INDEX = "clear_index"
    REFRESH_INDEX = "refresh_index"


@dataclass
class AgentContext:
    """Context for permission checks."""

    agent_id: UUID
    role: AgentRole
    team: Team | None = None
    slug: str | None = None  # Agent slug (e.g., "be-dev-1")

    @property
    def level(self) -> PermissionLevel:
        return ROLE_LEVELS.get(self.role, PermissionLevel.CELL_MEMBER)


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


# Per HOMELAB_TEAM_V0.md Section 12.3
TASK_PERMISSIONS: dict[AgentRole, set[str]] = {
    # System role (orchestrator) has full access for internal operations
    AgentRole.SYSTEM: {
        TaskAction.VIEW_ALL,
        TaskAction.CREATE,
        TaskAction.ASSIGN,
        TaskAction.CLAIM,
        TaskAction.UPDATE_OWN,
        TaskAction.CLOSE,
        TaskAction.CHANGE_PRIORITY,
    },
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
        TaskAction.CLAIM,  # Required to assign tasks via claim endpoint
        TaskAction.CLOSE,
        TaskAction.CHANGE_PRIORITY,
    },
    AgentRole.CELL_PM: {
        TaskAction.VIEW_OWN,  # Own cell only
        TaskAction.CREATE,
        TaskAction.ASSIGN,
        TaskAction.CLAIM,  # Required to assign tasks via claim endpoint
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
        TaskAction.CLOSE,  # Documenters complete tasks after documentation
    },
}


# Knowledge Base permissions - defines who can perform KB operations
KB_PERMISSIONS: dict[AgentRole, set[str]] = {
    AgentRole.CEO: {
        KBAction.INDEX_CODE,
        KBAction.INDEX_DOCS,
        KBAction.SEARCH,
        KBAction.QUERY,
        KBAction.VIEW_STATS,
        KBAction.CLEAR_INDEX,
        KBAction.REFRESH_INDEX,
    },
    AgentRole.PRODUCT_OWNER: {
        KBAction.INDEX_DOCS,
        KBAction.SEARCH,
        KBAction.QUERY,
        KBAction.VIEW_STATS,
    },
    AgentRole.HEAD_MARKETING: {
        KBAction.INDEX_DOCS,
        KBAction.SEARCH,
        KBAction.QUERY,
    },
    AgentRole.AUDITOR: {
        KBAction.SEARCH,
        KBAction.QUERY,
        KBAction.VIEW_STATS,
    },
    AgentRole.MAIN_PM: {
        KBAction.INDEX_CODE,
        KBAction.INDEX_DOCS,
        KBAction.SEARCH,
        KBAction.QUERY,
        KBAction.VIEW_STATS,
        KBAction.CLEAR_INDEX,
        KBAction.REFRESH_INDEX,
    },
    AgentRole.CELL_PM: {
        KBAction.INDEX_CODE,
        KBAction.INDEX_DOCS,
        KBAction.SEARCH,
        KBAction.QUERY,
        KBAction.VIEW_STATS,
    },
    AgentRole.DEVELOPER: {
        KBAction.INDEX_CODE,
        KBAction.INDEX_DOCS,
        KBAction.SEARCH,
        KBAction.QUERY,
    },
    AgentRole.QA: {
        KBAction.SEARCH,
        KBAction.QUERY,
    },
    AgentRole.DOCUMENTER: {
        KBAction.INDEX_DOCS,
        KBAction.SEARCH,
        KBAction.QUERY,
    },
}
