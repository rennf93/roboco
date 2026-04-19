"""
Agent Configuration

Single source of truth for agent roles, teams, and cell memberships.
All enforcement modules and MCP servers should import from here.

PERMISSION ARCHITECTURE
-----------------------
The system uses TWO complementary permission layers:

1. MCP Layer (this module - agents_config.py):
   - Controls which MCP tools agents can access
   - Uses role-based helper functions: can_create_tasks(), can_cancel_tasks(), etc.
   - Works with agent slugs/UUIDs directly
   - Determines tool visibility at MCP server registration time

2. API Layer (roboco/services/permissions.py):
   - Controls fine-grained API endpoint access
   - Uses TaskAction enum and PermissionService class
   - Works with AgentContext and Team enums
   - Validates at request time with team-scoped checks

Both layers derive from the same source data (role definitions here) but serve
different purposes. MCP is coarse-grained (tool-level), API is fine-grained
(action + team context).
"""

from typing import Final

from roboco.models.base import NotificationPriority, NotificationType
from roboco.seeds.initial_data import AGENT_UUIDS

# Reverse mapping: UUID -> slug (computed from seeds)
_UUID_TO_SLUG: Final[dict[str, str]] = {
    uuid: slug for slug, uuid in AGENT_UUIDS.items()
}


def _resolve_to_slug(agent_id: str) -> str:
    """Resolve agent ID (UUID or slug) to slug."""
    return _UUID_TO_SLUG.get(agent_id, agent_id)


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
    "ux-dev-1": "developer",
    "ux-dev-2": "developer",
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
    # UX/UI cell (matches Team.UX_UI = "ux_ui")
    "ux-dev-1": "ux_ui",
    "ux-dev-2": "ux_ui",
    "ux-qa": "ux_ui",
    "ux-pm": "ux_ui",
    "ux-doc": "ux_ui",
    # Management has no team
}


CELL_MEMBERS: Final[dict[str, list[str]]] = {
    "backend": ["be-dev-1", "be-dev-2", "be-qa", "be-pm", "be-doc"],
    "frontend": ["fe-dev-1", "fe-dev-2", "fe-qa", "fe-pm", "fe-doc"],
    "ux_ui": ["ux-dev-1", "ux-dev-2", "ux-qa", "ux-pm", "ux-doc"],
}


# All agent IDs
ALL_AGENTS: Final[list[str]] = list(AGENT_ROLE_MAP.keys())

# Board members
BOARD_MEMBERS: Final[list[str]] = ["product-owner", "head-marketing", "auditor"]

# All PMs
ALL_PMS: Final[list[str]] = ["be-pm", "fe-pm", "ux-pm", "main-pm"]

# All by role (cross-cell)
ALL_DEVS: Final[list[str]] = [
    "be-dev-1",
    "be-dev-2",
    "fe-dev-1",
    "fe-dev-2",
    "ux-dev-1",
    "ux-dev-2",
]
ALL_QA: Final[list[str]] = ["be-qa", "fe-qa", "ux-qa"]
ALL_DOCS: Final[list[str]] = ["be-doc", "fe-doc", "ux-doc"]
CELL_PMS: Final[list[str]] = ["be-pm", "fe-pm", "ux-pm"]

# PM-capable roles (can create and assign tasks)
PM_ROLES: Final[set[str]] = {
    "cell_pm",
    "main_pm",
    "product_owner",
    "head_marketing",
    "ceo",
}

# Escalation chain - who each agent escalates to
ESCALATION_CHAIN: Final[dict[str, str]] = {
    # Developers → Cell PM
    "be-dev-1": "be-pm",
    "be-dev-2": "be-pm",
    "fe-dev-1": "fe-pm",
    "fe-dev-2": "fe-pm",
    "ux-dev-1": "ux-pm",
    "ux-dev-2": "ux-pm",
    # QA → Cell PM
    "be-qa": "be-pm",
    "fe-qa": "fe-pm",
    "ux-qa": "ux-pm",
    # Documenters → Cell PM
    "be-doc": "be-pm",
    "fe-doc": "fe-pm",
    "ux-doc": "ux-pm",
    # Cell PM → Main PM
    "be-pm": "main-pm",
    "fe-pm": "main-pm",
    "ux-pm": "main-pm",
    # Main PM → Product Owner
    "main-pm": "product-owner",
    # Product Owner → CEO (final escalation)
    "product-owner": "ceo",
    "head-marketing": "ceo",
    "auditor": "ceo",
}


# =============================================================================
# HELPER FUNCTIONS
# =============================================================================


def get_agent_role(agent_id: str) -> str:
    """Get the role for an agent. Accepts both UUID and slug."""
    slug = _resolve_to_slug(agent_id)
    return AGENT_ROLE_MAP.get(slug, "unknown")


def get_agent_team(agent_id: str) -> str | None:
    """Get the team for an agent. Accepts both UUID and slug."""
    slug = _resolve_to_slug(agent_id)
    return AGENT_TEAM_MAP.get(slug)


def get_agent_cell(agent_id: str) -> str | None:
    """Get the cell an agent belongs to. Accepts both UUID and slug."""
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


def is_ceo(agent_id: str) -> bool:
    """Check if agent is CEO (has full bypass on all permissions)."""
    return get_agent_role(agent_id) == "ceo"


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


def can_create_tasks(agent_id: str) -> bool:
    """Check if agent can create tasks (PMs and management only)."""
    role = get_agent_role(agent_id)
    return role in PM_ROLES


def can_assign_tasks(agent_id: str) -> bool:
    """Check if agent can assign tasks (PMs and management only)."""
    role = get_agent_role(agent_id)
    return role in PM_ROLES


# Cancel roles match task_lifecycle.py - CEO and Auditor cannot cancel (they observe)
_CANCEL_ROLES: Final[set[str]] = {
    "cell_pm",
    "main_pm",
    "product_owner",
    "head_marketing",
}


def can_cancel_tasks(agent_id: str) -> bool:
    """Check if agent can cancel tasks (PMs and board, not CEO/Auditor)."""
    role = get_agent_role(agent_id)
    return role in _CANCEL_ROLES


def get_escalation_target(agent_id: str) -> str | None:
    """Get the escalation target for an agent."""
    return ESCALATION_CHAIN.get(agent_id)


def get_pm_for_team(team: str) -> str | None:
    """Get the cell PM for a team."""
    team_to_pm = {
        "backend": "be-pm",
        "frontend": "fe-pm",
        "ux_ui": "ux-pm",
    }
    return team_to_pm.get(team)


def get_pm_for_agent(agent_id: str) -> str | None:
    """
    Get the PM responsible for an agent.

    - For cell members: their cell PM
    - For cell PMs: main-pm
    - For main PM: product-owner
    """
    role = get_agent_role(agent_id)

    # Cell PM escalates to main-pm
    if role == "cell_pm":
        return "main-pm"

    # Main PM escalates to product-owner
    if role == "main_pm":
        return "product-owner"

    # Everyone else escalates to their cell PM
    return get_escalation_target(agent_id)


# =============================================================================
# CHANNEL ACCESS RULES
# =============================================================================

CHANNEL_ACCESS: Final[dict[str, dict[str, list[str]]]] = {
    # Cell channels - members + main-pm read/write, auditor silent
    "backend-cell": {
        "read": [*CELL_MEMBERS["backend"], "main-pm"],
        "write": [*CELL_MEMBERS["backend"], "main-pm"],
        "silent": ["auditor"],
    },
    "frontend-cell": {
        "read": [*CELL_MEMBERS["frontend"], "main-pm"],
        "write": [*CELL_MEMBERS["frontend"], "main-pm"],
        "silent": ["auditor"],
    },
    "uxui-cell": {
        "read": [*CELL_MEMBERS["ux_ui"], "main-pm"],
        "write": [*CELL_MEMBERS["ux_ui"], "main-pm"],
        "silent": ["auditor"],
    },
    # Cross-cell role channels
    # Cell members read/write their role channel
    # Cell PMs read/write ALL cross-cell channels for coordination
    "dev-all": {
        "read": [*ALL_DEVS, *ALL_QA, *ALL_DOCS, *CELL_PMS, "main-pm"],
        "write": [*ALL_DEVS, *CELL_PMS, "main-pm"],
        "silent": ["auditor"],
    },
    "qa-all": {
        "read": [*ALL_QA, *ALL_DEVS, *ALL_DOCS, *CELL_PMS, "main-pm"],
        "write": [*ALL_QA, *CELL_PMS],
        "silent": ["auditor"],
    },
    "pm-all": {
        "read": [*CELL_PMS, "main-pm"],
        "write": [*CELL_PMS, "main-pm"],
        "silent": ["auditor"],
    },
    "doc-all": {
        "read": [*ALL_DOCS, *CELL_PMS, "main-pm"],
        "write": [*ALL_DOCS, *CELL_PMS],
        "silent": ["auditor"],
    },
    # Management channels
    "main-pm-board": {
        "read": ["main-pm", "product-owner", "head-marketing", "auditor"],
        "write": ["main-pm", "product-owner", "head-marketing", "auditor"],
        "silent": [],
    },
    "board-private": {
        "read": ["product-owner", "head-marketing", "auditor", "ceo", "main-pm"],
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

# =============================================================================
# PERMISSION LEVEL HIERARCHY
# =============================================================================

# Maps role strings to permission level names
# This is the SINGLE SOURCE OF TRUTH for role hierarchy
# Used by PermissionService to build AgentRole -> PermissionLevel mapping
ROLE_PERMISSION_LEVELS: Final[dict[str, str]] = {
    "system": "CEO",  # System/orchestrator has CEO-level access for internal operations
    "ceo": "CEO",
    "product_owner": "BOARD",
    "head_marketing": "BOARD",
    "auditor": "AUDITOR",
    "main_pm": "MAIN_PM",
    "cell_pm": "CELL_PM",
    "developer": "CELL_MEMBER",
    "qa": "CELL_MEMBER",
    "documenter": "CELL_MEMBER",
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

VALID_NOTIFICATION_TYPES: Final[frozenset[str]] = frozenset(
    t.value for t in NotificationType
)
VALID_NOTIFICATION_PRIORITIES: Final[frozenset[str]] = frozenset(
    p.value for p in NotificationPriority
)


# =============================================================================
# A2A AGENT SKILLS (for Agent Cards)
# =============================================================================

# Skills define what each role can do - used for A2A discovery
ROLE_SKILLS: Final[dict[str, list[dict[str, str | list[str]]]]] = {
    "developer": [
        {
            "id": "code_implementation",
            "name": "Code Implementation",
            "description": "Implement features, fix bugs, write production code",
            "tags": ["coding", "implementation", "bugfix"],
        },
        {
            "id": "code_review",
            "name": "Code Review",
            "description": "Review code changes and provide feedback",
            "tags": ["review", "feedback"],
        },
        {
            "id": "technical_research",
            "name": "Technical Research",
            "description": "Research technical solutions and approaches",
            "tags": ["research", "analysis"],
        },
    ],
    "qa": [
        {
            "id": "code_review",
            "name": "Code Review",
            "description": "Review code for bugs, security issues, and quality",
            "tags": ["review", "quality", "security"],
        },
        {
            "id": "test_validation",
            "name": "Test Validation",
            "description": "Validate test coverage and test quality",
            "tags": ["testing", "validation"],
        },
        {
            "id": "security_audit",
            "name": "Security Audit",
            "description": "Audit code for security vulnerabilities",
            "tags": ["security", "audit"],
        },
    ],
    "documenter": [
        {
            "id": "documentation",
            "name": "Documentation",
            "description": "Create and maintain documentation",
            "tags": ["docs", "writing"],
        },
        {
            "id": "handoff_review",
            "name": "Handoff Review",
            "description": "Review and document task handoffs",
            "tags": ["handoff", "review"],
        },
    ],
    "cell_pm": [
        {
            "id": "task_management",
            "name": "Task Management",
            "description": "Create, assign, and manage tasks within the cell",
            "tags": ["planning", "coordination"],
        },
        {
            "id": "blocker_resolution",
            "name": "Blocker Resolution",
            "description": "Help resolve blockers and coordinate resources",
            "tags": ["support", "coordination"],
        },
        {
            "id": "qa_coordination",
            "name": "QA Coordination",
            "description": "Coordinate QA reviews and approvals",
            "tags": ["qa", "approval"],
        },
    ],
    "main_pm": [
        {
            "id": "task_triage",
            "name": "Task Triage",
            "description": "Triage and distribute tasks to cell PMs",
            "tags": ["triage", "distribution"],
        },
        {
            "id": "cross_cell_coordination",
            "name": "Cross-Cell Coordination",
            "description": "Coordinate work across multiple cells",
            "tags": ["coordination", "cross-team"],
        },
        {
            "id": "escalation_handling",
            "name": "Escalation Handling",
            "description": "Handle escalated issues from cell PMs",
            "tags": ["escalation", "support"],
        },
    ],
    "product_owner": [
        {
            "id": "requirements_clarification",
            "name": "Requirements Clarification",
            "description": "Clarify product requirements and priorities",
            "tags": ["requirements", "product"],
        },
        {
            "id": "feature_approval",
            "name": "Feature Approval",
            "description": "Approve feature implementations",
            "tags": ["approval", "product"],
        },
    ],
    "head_marketing": [
        {
            "id": "market_analysis",
            "name": "Market Analysis",
            "description": "Provide market context and analysis",
            "tags": ["marketing", "analysis"],
        },
    ],
    "auditor": [
        {
            "id": "quality_audit",
            "name": "Quality Audit",
            "description": "Audit quality and compliance",
            "tags": ["audit", "quality"],
        },
    ],
}


def get_agent_skills(agent_id: str) -> list[dict]:
    """Get A2A skills for an agent based on their role."""
    role = get_agent_role(agent_id)
    return list(ROLE_SKILLS.get(role, []))


# =============================================================================
# A2A PERMISSION ENFORCEMENT
# =============================================================================
# A2A follows the same hierarchy as escalations and notifications:
# - Within cell: Direct A2A allowed
# - Cross-cell: Must go through Cell PM → Main PM
# - To board: Must go through Main PM
# - To CEO: Must go through board

# Roles that can reach each other directly (CEO is human - use notifications)
_BOARD_ROLES: Final[frozenset[str]] = frozenset(
    {"product_owner", "head_marketing", "auditor", "main_pm"}
)
_MAIN_PM_TARGETS: Final[frozenset[str]] = frozenset(
    {"cell_pm", "main_pm", "product_owner", "head_marketing", "auditor"}
)


def _check_cell_pm_a2a(
    from_team: str | None, to_agent: str, to_role: str, to_team: str | None
) -> tuple[bool, str | None]:
    """Check A2A permissions for cell PM."""
    # Own cell, other PMs, or main-pm
    if to_team == from_team or to_role in ("cell_pm", "main_pm"):
        return True, None
    # Board/CEO - escalate
    if to_role in _BOARD_ROLES:
        return False, f"Cell PMs cannot A2A {to_role}. Escalate through main-pm."
    # Other cell members
    return False, f"Cannot A2A {to_agent} (different cell). Use main-pm."


def _check_cell_member_a2a(
    from_agent: str, from_team: str, to_agent: str, to_role: str, to_team: str | None
) -> tuple[bool, str | None]:
    """Check A2A permissions for cell members (dev, qa, doc)."""
    cell_pm = get_pm_for_team(from_team)
    # Same cell - allowed
    if to_team == from_team:
        return True, None
    # Cross-cell
    if to_team:
        target_pm = get_pm_for_team(to_team)
        return (
            False,
            f"Cannot A2A {to_agent} (cell: {to_team}). "
            f"Ask {cell_pm} to coordinate with {target_pm}.",
        )
    # Management - not direct
    return False, f"Cannot A2A {to_role}. Route: {from_agent} → {cell_pm} → main-pm."


def _check_main_pm_a2a(to_role: str, to_team: str | None) -> tuple[bool, str | None]:
    """Check A2A permissions for main PM."""
    if to_role in _MAIN_PM_TARGETS:
        return True, None
    pm = get_pm_for_team(to_team) if to_team else "cell-pm"
    return False, f"Main PM cannot A2A {to_role}s. Route through {pm or 'cell-pm'}."


def can_a2a_direct(from_agent: str, to_agent: str) -> tuple[bool, str | None]:
    """
    Check if from_agent can send A2A directly to to_agent.

    Returns (allowed, error_message). Error explains who to contact instead.
    """
    from_role = get_agent_role(from_agent)
    to_role = get_agent_role(to_agent)
    from_team = get_agent_team(from_agent)
    to_team = get_agent_team(to_agent)

    # CEO is human - cannot A2A, use notifications
    if to_role == "ceo":
        return False, "CEO is human. Use roboco_notify_send() instead of A2A."

    # Board → board/main-pm (not CEO, not cells directly)
    if from_role in ("product_owner", "head_marketing", "auditor"):
        return (
            (True, None)
            if to_role in _BOARD_ROLES
            else (False, f"Board cannot A2A {to_role}s. Route through main-pm.")
        )

    # Dispatch to role-specific handlers
    handlers: dict[str, tuple[bool, str | None]] = {
        "main_pm": _check_main_pm_a2a(to_role, to_team),
        "cell_pm": _check_cell_pm_a2a(from_team, to_agent, to_role, to_team),
    }
    if from_role in handlers:
        return handlers[from_role]

    # Cell members - use helper
    if from_team:
        return _check_cell_member_a2a(from_agent, from_team, to_agent, to_role, to_team)

    return False, f"A2A from {from_agent} to {to_agent} not permitted."


def get_a2a_route_hint(from_agent: str, to_agent: str) -> str:
    """Get a hint for how to properly route an A2A message."""
    to_role = get_agent_role(to_agent)
    from_team = get_agent_team(from_agent)
    to_team = get_agent_team(to_agent)

    # CEO is human - no A2A route, use notifications
    if to_role == "ceo":
        return "CEO is human. Use roboco_notify_send() for CEO communication."

    # Cross-cell routing
    if from_team and to_team and from_team != to_team:
        from_pm = get_pm_for_team(from_team)
        to_pm = get_pm_for_team(to_team)
        return f"Route: {from_agent}→{from_pm}→main-pm→{to_pm}→{to_agent}"

    # Cell member to management
    if from_team:
        cell_pm = get_pm_for_team(from_team)
        return f"Route: {from_agent}→{cell_pm}→main-pm→board"

    return "Use roboco_task_escalate() for proper escalation."
