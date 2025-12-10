"""
Agent Configuration

Single source of truth for agent roles, teams, and cell memberships.
All enforcement modules and MCP servers should import from here.
"""

from typing import Final

# =============================================================================
# AGENT ROLE MAPPINGS
# =============================================================================

AGENT_ROLE_MAP: Final[dict[str, str]] = {
    # Backend cell
    "be-dev-1": "developer",
    "be-dev-2": "developer",
    "be-qa": "qa",
    "be-pm": "cell_pm",
    "be-doc": "documenter",
    # Frontend cell
    "fe-dev-1": "developer",
    "fe-dev-2": "developer",
    "fe-qa": "qa",
    "fe-pm": "cell_pm",
    "fe-doc": "documenter",
    # UX/UI cell
    "ux-dev": "developer",
    "ux-qa": "qa",
    "ux-pm": "cell_pm",
    "ux-doc": "documenter",
    # Management / Board
    "main-pm": "main_pm",
    "product-owner": "product_owner",
    "head-marketing": "head_marketing",
    "auditor": "auditor",
    "ceo": "ceo",
}


AGENT_TEAM_MAP: Final[dict[str, str]] = {
    # Backend cell
    "be-dev-1": "backend",
    "be-dev-2": "backend",
    "be-qa": "backend",
    "be-pm": "backend",
    "be-doc": "backend",
    # Frontend cell
    "fe-dev-1": "frontend",
    "fe-dev-2": "frontend",
    "fe-qa": "frontend",
    "fe-pm": "frontend",
    "fe-doc": "frontend",
    # UX/UI cell
    "ux-dev": "uxui",
    "ux-qa": "uxui",
    "ux-pm": "uxui",
    "ux-doc": "uxui",
    # Management has no team
}


CELL_MEMBERS: Final[dict[str, list[str]]] = {
    "backend": ["be-dev-1", "be-dev-2", "be-qa", "be-pm", "be-doc"],
    "frontend": ["fe-dev-1", "fe-dev-2", "fe-qa", "fe-pm", "fe-doc"],
    "uxui": ["ux-dev", "ux-qa", "ux-pm", "ux-doc"],
}


# All agent IDs
ALL_AGENTS: Final[list[str]] = list(AGENT_ROLE_MAP.keys())

# Board members
BOARD_MEMBERS: Final[list[str]] = ["product-owner", "head-marketing", "auditor"]

# All PMs
ALL_PMS: Final[list[str]] = ["be-pm", "fe-pm", "ux-pm", "main-pm"]


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def get_agent_role(agent_id: str) -> str:
    """Get the role for an agent."""
    return AGENT_ROLE_MAP.get(agent_id, "unknown")


def get_agent_team(agent_id: str) -> str | None:
    """Get the team for an agent."""
    return AGENT_TEAM_MAP.get(agent_id)


def get_agent_cell(agent_id: str) -> str | None:
    """Get the cell an agent belongs to (alias for get_agent_team)."""
    return get_agent_team(agent_id)


def get_cell_members(cell: str) -> list[str]:
    """Get all members of a cell."""
    return CELL_MEMBERS.get(cell, [])


def is_pm(agent_id: str) -> bool:
    """Check if agent is a PM (cell PM or main PM)."""
    role = get_agent_role(agent_id)
    return role in ("cell_pm", "main_pm")


def is_board_member(agent_id: str) -> bool:
    """Check if agent is a board member."""
    return agent_id in BOARD_MEMBERS


def is_management(agent_id: str) -> bool:
    """Check if agent is in management (PM, Board, CEO)."""
    role = get_agent_role(agent_id)
    return role in (
        "cell_pm",
        "main_pm",
        "product_owner",
        "head_marketing",
        "auditor",
        "ceo",
    )


def can_send_notifications(agent_id: str) -> bool:
    """Check if agent can send notifications (PMs, Board, Auditor, CEO)."""
    role = get_agent_role(agent_id)
    return role in (
        "cell_pm",
        "main_pm",
        "product_owner",
        "head_marketing",
        "auditor",
        "ceo",
    )


# =============================================================================
# CHANNEL ACCESS RULES
# =============================================================================

CHANNEL_ACCESS: Final[dict[str, dict[str, list[str]]]] = {
    # Cell channels - members read/write, auditor silent
    "backend-cell": {
        "read": CELL_MEMBERS["backend"],
        "write": CELL_MEMBERS["backend"],
        "silent": ["auditor"],
    },
    "frontend-cell": {
        "read": CELL_MEMBERS["frontend"],
        "write": CELL_MEMBERS["frontend"],
        "silent": ["auditor"],
    },
    "uxui-cell": {
        "read": CELL_MEMBERS["uxui"],
        "write": CELL_MEMBERS["uxui"],
        "silent": ["auditor"],
    },
    # Cross-cell role channels
    "dev-all": {
        "read": ["be-dev-1", "be-dev-2", "fe-dev-1", "fe-dev-2", "ux-dev"],
        "write": ["be-dev-1", "be-dev-2", "fe-dev-1", "fe-dev-2", "ux-dev"],
        "silent": ["auditor"],
    },
    "qa-all": {
        "read": ["be-qa", "fe-qa", "ux-qa"],
        "write": ["be-qa", "fe-qa", "ux-qa"],
        "silent": ["auditor"],
    },
    "pm-all": {
        "read": ["be-pm", "fe-pm", "ux-pm", "main-pm"],
        "write": ["be-pm", "fe-pm", "ux-pm", "main-pm"],
        "silent": ["auditor"],
    },
    "doc-all": {
        "read": ["be-doc", "fe-doc", "ux-doc"],
        "write": ["be-doc", "fe-doc", "ux-doc"],
        "silent": ["auditor"],
    },
    # Management channels
    "main-pm-board": {
        "read": ["main-pm", "product-owner", "head-marketing", "auditor"],
        "write": ["main-pm", "product-owner", "head-marketing", "auditor"],
        "silent": [],
    },
    "board-private": {
        "read": ["product-owner", "head-marketing", "auditor", "ceo"],
        "write": ["product-owner", "head-marketing", "auditor", "ceo"],
        "silent": [],
    },
    # Broadcast channels
    "announcements": {
        "read": ALL_AGENTS,
        "write": ["main-pm", "product-owner", "head-marketing", "ceo"],
        "silent": [],
    },
    "all-hands": {
        "read": ALL_AGENTS,
        "write": ALL_AGENTS,
        "silent": [],
    },
}


# =============================================================================
# NOTIFICATION PERMISSIONS
# =============================================================================

NOTIFICATION_PERMISSIONS: Final[dict[str, dict]] = {
    # Cell PMs can notify their own cell members
    "cell_pm": {
        "can_send": True,
        "scope": "cell",
    },
    # Main PM can notify anyone
    "main_pm": {
        "can_send": True,
        "scope": "all",
    },
    # Board can notify management chain
    "product_owner": {
        "can_send": True,
        "scope": ["main-pm", "head-marketing", "auditor", "ceo"],
    },
    "head_marketing": {
        "can_send": True,
        "scope": ["main-pm", "product-owner", "auditor", "ceo"],
    },
    # Auditor can notify anyone
    "auditor": {
        "can_send": True,
        "scope": "all",
    },
    # CEO can notify anyone
    "ceo": {
        "can_send": True,
        "scope": "all",
    },
    # Developers CANNOT send notifications
    "developer": {
        "can_send": False,
    },
    # QA CANNOT send notifications
    "qa": {
        "can_send": False,
    },
    # Documenters CANNOT send notifications
    "documenter": {
        "can_send": False,
    },
}
