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
