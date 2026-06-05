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

import hashlib
import hmac
import os
from typing import Final

from roboco.foundation import identity as _foundation
from roboco.foundation.policy import communications as _comms
from roboco.models.base import NotificationPriority, NotificationType
from roboco.seeds.initial_data import AGENT_UUIDS, CEO_AGENT_ID

# Env var containing the HMAC secret used to sign agent auth tokens.
# Must be set in orchestrator + API container environments; if missing,
# token verification refuses every token (fail-closed). Generate with:
#   python -c 'import secrets; print(secrets.token_hex(32))'
_AUTH_SECRET_ENV: Final[str] = "ROBOCO_AGENT_AUTH_SECRET"


def _auth_secret() -> bytes | None:
    """Return the HMAC secret bytes, or None when unset."""
    v = os.environ.get(_AUTH_SECRET_ENV, "")
    return v.encode("utf-8") if v else None


def _signing_payload(agent_id: str, role: str, team: str) -> bytes:
    """Canonical message for HMAC — all inputs lower-cased and stripped."""
    parts = (
        (agent_id or "").strip().lower(),
        (role or "").strip().lower(),
        (team or "").strip().lower(),
    )
    return ":".join(parts).encode("utf-8")


def issue_agent_token(agent_id: str, role: str, team: str = "") -> str:
    """Mint an auth token the orchestrator injects into an agent's env.

    The token is a hex HMAC-SHA256 of `agent_id:role:team` signed with
    ROBOCO_AGENT_AUTH_SECRET. It binds the agent's identity to the role
    and team headers — if the agent later lies about its role, the
    server-side HMAC won't match.
    """
    secret = _auth_secret()
    if not secret:
        # Unset secret ⇒ tokens are meaningless; return a sentinel the
        # verifier will reject. Caller should detect and log this.
        return "UNSIGNED"
    return hmac.new(
        secret, _signing_payload(agent_id, role, team), hashlib.sha256
    ).hexdigest()


def verify_agent_token(token: str, agent_id: str, role: str, team: str = "") -> bool:
    """Return True iff `token` is a valid HMAC for (agent_id, role, team).

    Fails closed when the secret is unset or the token is the UNSIGNED
    sentinel.
    """
    secret = _auth_secret()
    if not secret or not token or token == "UNSIGNED":
        return False
    expected = hmac.new(
        secret, _signing_payload(agent_id, role, team), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, token)


def issue_panel_token() -> str:
    """Mint the token the control panel presents to act as the CEO.

    The panel calls the API as the CEO identity — ``X-Agent-Id`` = the CEO
    UUID, ``X-Agent-Role`` = ``ceo``, and no team header — so the token is
    signed for exactly those values (empty team). In secure mode nginx injects
    it as ``X-Agent-Token`` so the browser never holds the signing secret;
    this is just the existing per-agent token issued for the CEO identity, so
    the verification path is unchanged. Returns ``UNSIGNED`` when the secret is
    unset (same fail-closed contract as ``issue_agent_token``).
    """
    return issue_agent_token(CEO_AGENT_ID, "ceo", "")


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

# Agent catalog data is canonicalized in roboco/foundation/identity.py.
# These string-keyed maps are kept for backwards compatibility with code
# that types role/team as `str` rather than the foundation enums.
# Derived at module load — adding an agent edits foundation/identity.py only.
AGENT_ROLE_MAP: dict[str, str] = {
    slug: row.role.value
    for slug, row in _foundation.AGENTS.items()
    if row.role != _foundation.Role.SYSTEM  # exclude sentinel from string-keyed map
}

AGENT_TEAM_MAP: dict[str, str] = {
    slug: row.team.value
    for slug, row in _foundation.AGENTS.items()
    if row.role != _foundation.Role.SYSTEM
}

CELL_MEMBERS: dict[str, list[str]] = {
    team.value: sorted(_foundation.slugs_for_team(team))
    for team in sorted(_foundation.CELL_TEAMS, key=lambda t: t.value)
}


# All agent IDs
ALL_AGENTS: Final[list[str]] = list(AGENT_ROLE_MAP.keys())

# Board members
BOARD_MEMBERS: Final[list[str]] = ["product-owner", "head-marketing", "auditor"]

# All documenters (cross-cell) — used for docs-write workspace permissions
ALL_DOCS: Final[list[str]] = ["be-doc", "fe-doc", "ux-doc"]

# `PM_ROLES` is canonical in foundation.identity (CELL_PM + MAIN_PM only).
# This file's historical 5-role set is renamed to TASK_CREATOR_ROLES — it
# represents "roles that can call task.create", not the PM hierarchy.
# StrEnum members hash like their .value strings, so `role_str in TASK_CREATOR_ROLES`
# still works for str inputs from get_agent_role().
TASK_CREATOR_ROLES: Final[frozenset[_foundation.Role]] = frozenset(
    {
        _foundation.Role.CELL_PM,
        _foundation.Role.MAIN_PM,
        _foundation.Role.PRODUCT_OWNER,
        _foundation.Role.HEAD_MARKETING,
        _foundation.Role.CEO,
    }
)

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
    """Whether this agent's role may call notify(). Canonical in foundation."""
    try:
        return _foundation.Role(get_agent_role(agent_id)) in _comms.NOTIFY_SENDER_ROLES
    except ValueError:
        return False


def can_create_tasks(agent_id: str) -> bool:
    """Check if agent can create tasks (PMs, board, and CEO)."""
    role = get_agent_role(agent_id)
    return role in TASK_CREATOR_ROLES


def can_assign_tasks(agent_id: str) -> bool:
    """Check if agent can assign tasks (PMs, board, and CEO)."""
    role = get_agent_role(agent_id)
    return role in TASK_CREATOR_ROLES


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
#
# Channel ACL is canonicalized in foundation.policy.communications.CHANNELS.
# This slug-keyed dict-of-string-lists derives from the role-keyed foundation
# data. Adding a channel or changing its membership edits foundation.CHANNELS;
# this dict updates at module load.
#
# Derivation rules:
#   - read:   roles in (read_roles - silent_roles), filtered by team_scope
#   - write:  roles in write_roles, filtered by team_scope
#   - silent: roles in silent_roles, filtered by team_scope
# Cross-cell roles (MAIN_PM, AUDITOR, CEO, board) are not subject to team_scope;
# only cell-member roles (DEVELOPER/QA/DOCUMENTER/CELL_PM) are filtered.

# Cell-member roles subject to team_scope filtering. Lifted to module scope so
# tests and downstream consumers can introspect the rule.
_TEAM_SCOPED_ROLES: Final[frozenset[_foundation.Role]] = frozenset(
    {
        _foundation.Role.DEVELOPER,
        _foundation.Role.QA,
        _foundation.Role.DOCUMENTER,
        _foundation.Role.CELL_PM,
    }
)


def _slugs_for_role_set(
    role_set: frozenset[_foundation.Role],
    team_scope: _foundation.Team | None,
) -> list[str]:
    """Expand a role-set to sorted agent slugs, honoring optional team_scope.

    A slug qualifies when its role is in `role_set` AND, if its role is in
    _TEAM_SCOPED_ROLES and team_scope is set, its team matches team_scope.
    The system sentinel is always excluded.
    """
    out: list[str] = []
    for slug, row in _foundation.AGENTS.items():
        if slug == "system":
            continue
        if row.role not in role_set:
            continue
        if (
            team_scope is not None
            and row.role in _TEAM_SCOPED_ROLES
            and row.team != team_scope
        ):
            continue
        out.append(slug)
    return sorted(out)


CHANNEL_ACCESS: Final[dict[str, dict[str, list[str]]]] = {
    slug: {
        "read": _slugs_for_role_set(
            spec.read_roles - spec.silent_roles, spec.team_scope
        ),
        "write": _slugs_for_role_set(spec.write_roles, spec.team_scope),
        "silent": _slugs_for_role_set(spec.silent_roles, spec.team_scope),
    }
    for slug, spec in _comms.CHANNELS.items()
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
# Sender allowlist now lives in foundation.policy.communications.NOTIFY_SENDER_ROLES.
# Scope rules (cell / all / list) live in services/permissions.py since they
# depend on AgentContext (role + team) — not pure foundation data.

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

# Board roles (PO + Head Marketing + Auditor) — derived from foundation.
# Main PM is intentionally NOT in this set; main_pm is a layer above cells
# but below the board.
_BOARD_ROLES: Final[frozenset[_foundation.Role]] = _foundation.BOARD_ROLES
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
        return False, "CEO is human. Use notify() instead of A2A."

    # Board → board/main-pm (not CEO, not cells directly)
    if from_role in ("product_owner", "head_marketing", "auditor"):
        return (
            (True, None)
            if to_role in _BOARD_ROLES or to_role == "main_pm"
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
        return "CEO is human. Use notify() for CEO communication."

    # Cross-cell routing
    if from_team and to_team and from_team != to_team:
        from_pm = get_pm_for_team(from_team)
        to_pm = get_pm_for_team(to_team)
        return f"Route: {from_agent}→{from_pm}→main-pm→{to_pm}→{to_agent}"

    # Cell member to management
    if from_team:
        cell_pm = get_pm_for_team(from_team)
        return f"Route: {from_agent}→{cell_pm}→main-pm→board"

    return "Use escalate_up() for proper escalation."
