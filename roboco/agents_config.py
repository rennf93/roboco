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

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass
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


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def issue_agent_token(
    agent_id: str,
    role: str,
    team: str = "",
    *,
    ttl_seconds: float | None = None,
    now: float | None = None,
) -> str:
    """Mint an auth token the orchestrator injects into an agent's env.

    Static form (``ttl_seconds`` None): a hex HMAC-SHA256 of
    ``agent_id:role:team`` — backward-compatible, used by the panel's static
    ``ROBOCO_PANEL_AGENT_TOKEN``. Expiring form (``ttl_seconds`` set):
    ``{base64url(payload)}.{hmac}`` carrying ``id/role/team/iat/exp``, signed
    over the base64url payload so a stolen token is bounded by ``exp`` and
    refreshed at each spawn. Returns ``UNSIGNED`` when the secret is unset.
    """
    secret = _auth_secret()
    if not secret:
        return "UNSIGNED"
    aid = (agent_id or "").strip().lower()
    r = (role or "").strip().lower()
    t = (team or "").strip().lower()
    if ttl_seconds is None:
        return hmac.new(secret, f"{aid}:{r}:{t}".encode(), hashlib.sha256).hexdigest()
    ts = now if now is not None else time.time()
    payload = json.dumps(
        {"id": aid, "role": r, "team": t, "iat": ts, "exp": ts + ttl_seconds},
        separators=(",", ":"),
    ).encode("utf-8")
    pb = _b64url_encode(payload)
    sig = hmac.new(secret, pb.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{pb}.{sig}"


def _verify_expiring_token(
    token: str,
    secret: bytes,
    expected: tuple[str, str, str],
    now: float | None,
) -> bool:
    """Verify the ``{payload}.{sig}`` expiring form against an (id,role,team)."""
    pb, _, sig = token.rpartition(".")
    expected_sig = hmac.new(secret, pb.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_sig, sig):
        return False
    try:
        payload = json.loads(_b64url_decode(pb))
    except (ValueError, KeyError):
        return False
    exp = payload.get("exp")
    if not isinstance(exp, (int, float)):
        return False
    # Short-circuit like the original: time.time() is read only when the
    # (id, role, team) fields already match, never on every call.
    return (
        payload.get("id"),
        payload.get("role"),
        payload.get("team"),
    ) == expected and exp > (now if now is not None else time.time())


def verify_agent_token(
    token: str,
    agent_id: str,
    role: str,
    team: str = "",
    *,
    now: float | None = None,
) -> bool:
    """Return True iff ``token`` is a valid HMAC for (agent_id, role, team).

    Accepts both the static hex form and the expiring ``{payload}.{sig}`` form
    (detected by a ``.`` separator); the expiring form additionally requires
    ``exp > now``. Fails closed when the secret is unset or the token is the
    UNSIGNED sentinel.
    """
    secret = _auth_secret()
    if not secret or not token or token == "UNSIGNED":
        return False
    if "." in token:
        expected_id = (
            (agent_id or "").strip().lower(),
            (role or "").strip().lower(),
            (team or "").strip().lower(),
        )
        return _verify_expiring_token(token, secret, expected_id, now)
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


def _check_ceo_a2a(to_role: str) -> tuple[bool, str | None]:
    """Check A2A permissions for the CEO's asymmetric send-to-anyone reach.

    A target with no agent-comms surface (no dm/read_a2a on its manifest —
    auditor, pr_reviewer, prompter, secretary) can never read or answer a
    DM regardless of who sends it; the panel's New-DM dialog already
    excludes these roles client-side (EXCLUDE_NON_DM_ROLES), this is the
    server-side backstop so a direct API/A2A-service call can't bypass it.
    """
    if to_role in _comms.NO_COMMS_ROLES:
        return (
            False,
            f"'{to_role}' has no agent-comms surface (no dm/read_a2a) and "
            "cannot receive a DM.",
        )
    return True, None


def _check_pr_reviewer_a2a(to_role: str) -> tuple[bool, str | None]:
    """Check A2A permissions for a PR reviewer.

    The reviewer sits above cells (it reviews cross-cell assembled PRs), so it
    never shares a team with the owning PM the way QA does with its cell PM. Its
    one comms surface is delivering the in-path gate verdict (pr_fail
    change-requests) to that owning PM — cell PM for a cell→root PR, Main PM for
    a root→master PR. Everything else it posts on the PR itself, not via A2A.
    """
    if to_role in ("cell_pm", "main_pm"):
        return True, None
    return False, f"PR reviewers only A2A the owning PM, not {to_role}s."


def can_a2a_direct(from_agent: str, to_agent: str) -> tuple[bool, str | None]:
    """
    Check if from_agent can send A2A directly to to_agent.

    Returns (allowed, error_message). Error explains who to contact instead.
    """
    from_role = get_agent_role(from_agent)
    to_role = get_agent_role(to_agent)
    from_team = get_agent_team(from_agent)
    to_team = get_agent_team(to_agent)

    # The CEO (human, via the panel) may chime into any agent's A2A thread —
    # the one asymmetric rule in this matrix: CEO may send, nobody may
    # target CEO (except a no-comms target, see _check_ceo_a2a).
    if from_role == "ceo":
        return _check_ceo_a2a(to_role)

    # CEO is human - agents can never INITIATE with the CEO. The only path in
    # is a reply inside a conversation the CEO itself opened (enforced
    # statefully in A2AService.send_chat_message's reply budget — this
    # matrix stays stateless, so it blocks conversation *creation*
    # unconditionally as defense-in-depth).
    if to_role == "ceo":
        return (
            False,
            "CEO is human. You may only reply inside a conversation the "
            "CEO opened — use notify() otherwise.",
        )

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
        "pr_reviewer": _check_pr_reviewer_a2a(to_role),
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

    # PR reviewer only reaches the owning PM; everything else goes on the PR.
    if get_agent_role(from_agent) == "pr_reviewer":
        return "PR reviewers A2A only the owning PM; post other feedback on the PR."

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


# =============================================================================
# A2A SWITCHBOARD — ALLOWED AGENT PAIRS (CEO admin view)
# =============================================================================
# Static, stateless derivation from can_a2a_direct(): every unordered pair of
# A2A participants (agents plus the CEO's asymmetric panel reach; never the
# sentinel or the prompter/secretary) where at least one direction is
# permitted. This is the org-chart the CEO's A2A switchboard renders as pair
# cards — computed once at import time, since the matrix never changes at
# runtime. The route/service layer joins this list against live DB
# conversation data per request.


@dataclass(frozen=True)
class A2AAllowedPair:
    """One CEO-visible pair of agents allowed to A2A directly (>=1 direction).

    ``agent_a`` < ``agent_b`` lexically — the same canonical ordering
    A2AConversationTable and A2AService._canonical_pair use.
    """

    agent_a: str
    agent_b: str
    role_a: str
    team_a: str
    role_b: str
    team_b: str
    group_key: str


# Slugs eligible for the switchboard: excludes the system sentinel and the
# non-participant human roles (prompter, secretary). The CEO stays in — it is
# a real, asymmetric A2A participant (can_a2a_direct allows CEO → anyone via
# the panel's 1:1 DM flow), so its pairs must render on the switchboard.
_SWITCHBOARD_SLUGS: Final[list[str]] = sorted(
    slug
    for slug, row in _foundation.AGENTS.items()
    if slug != "system"
    and row.role not in (_foundation.Role.PROMPTER, _foundation.Role.SECRETARY)
)

_BOARD_ROLE_VALUES: Final[frozenset[str]] = frozenset(
    r.value for r in _foundation.BOARD_ROLES
)
_CELL_TEAM_VALUES: Final[frozenset[str]] = frozenset(
    t.value for t in _foundation.CELL_TEAMS
)


def _a2a_group_key(role_a: str, team_a: str, role_b: str, team_b: str) -> str:
    """Classify a pair into a stable section for the panel switchboard.

    - ``cell-<team>``: both agents share a delivery-cell team — each cell's
      own section (dev/qa/doc/pm/pr-reviewer talking within their cell).
    - ``pm-chain``: the coordination spine — cell_pm<->main_pm, or
      main_pm<->a board role (mirrors ESCALATION_CHAIN: cell_pm -> main-pm
      -> board).
    - ``board``: pure board-to-board pairs (product_owner/head_marketing/
      auditor).
    - ``ceo``: the CEO's asymmetric 1:1 reach into any agent (panel DMs).
    - ``cross``: everything else — chiefly a PR reviewer's lateral reach
      outside its own cell/pm (delivering a gate verdict to another cell's
      PM or to main-pm), which isn't part of the escalation spine.
    """
    roles = {role_a, role_b}
    if "ceo" in roles:
        return "ceo"
    if team_a == team_b and team_a in _CELL_TEAM_VALUES:
        return f"cell-{team_a}"
    if roles == {"cell_pm", "main_pm"}:
        return "pm-chain"
    if "main_pm" in roles and (
        role_a in _BOARD_ROLE_VALUES or role_b in _BOARD_ROLE_VALUES
    ):
        return "pm-chain"
    if role_a in _BOARD_ROLE_VALUES and role_b in _BOARD_ROLE_VALUES:
        return "board"
    return "cross"


def _compute_a2a_allowed_pairs() -> tuple[A2AAllowedPair, ...]:
    """Enumerate every unordered pair with >=1 allowed A2A direction."""
    pairs: list[A2AAllowedPair] = []
    for i, a in enumerate(_SWITCHBOARD_SLUGS):
        row_a = _foundation.AGENTS[a]
        for b in _SWITCHBOARD_SLUGS[i + 1 :]:
            row_b = _foundation.AGENTS[b]
            allowed_ab, _ = can_a2a_direct(a, b)
            allowed_ba, _ = can_a2a_direct(b, a)
            if not (allowed_ab or allowed_ba):
                continue
            pairs.append(
                A2AAllowedPair(
                    agent_a=a,
                    agent_b=b,
                    role_a=row_a.role.value,
                    team_a=row_a.team.value,
                    role_b=row_b.role.value,
                    team_b=row_b.team.value,
                    group_key=_a2a_group_key(
                        row_a.role.value,
                        row_a.team.value,
                        row_b.role.value,
                        row_b.team.value,
                    ),
                )
            )
    return tuple(pairs)


# Computed once at module load — see module docstring above.
A2A_ALLOWED_PAIRS: Final[tuple[A2AAllowedPair, ...]] = _compute_a2a_allowed_pairs()
