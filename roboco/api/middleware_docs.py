"""
Documentation Path Access Control

Enforces file access permissions at API level for documentation operations.
This middleware validates that agents can only access docs paths they are
authorized for based on their role and team.

Permission Matrix:
- internal/: CEO only
- standards/: READ: All | WRITE: PM + Documenter
- workflows/: READ: All | WRITE: Main PM only
- backend/: READ: Backend cell | WRITE: be-doc only
- frontend/: READ: Frontend cell | WRITE: fe-doc only
- ux_ui/: READ: UX/UI cell | WRITE: ux-doc only
- features/{team}/: Team + PM read | Team documenter write
- features/shared/: All read | Any documenter write
- bugs/{team}/: All read | Team documenter write
- self/: Board + PM read | Main PM + Auditor write
- initiatives/: Assigned teams (managed per-initiative)
"""

from typing import Final

import structlog

from roboco.agents_config import (
    AGENT_ROLE_MAP,
    AGENT_TEAM_MAP,
    ALL_DOCS,
    _resolve_to_slug,
)
from roboco.exceptions import PermissionDeniedError

logger = structlog.get_logger()

# =============================================================================
# PATH PERMISSION DEFINITIONS
# =============================================================================

# Special markers
ALL_AGENTS: Final[str] = "*"  # Any agent can access

# Minimum path parts for nested directories (features/backend, bugs/frontend)
MIN_NESTED_PATH_PARTS: Final[int] = 2

# Roles with full docs access (bypass all checks)
FULL_ACCESS_ROLES: Final[set[str]] = {"ceo"}

# Roles with read-all access (can read everything except internal)
READ_ALL_ROLES: Final[set[str]] = {"auditor", "main_pm"}

# Permission definitions by path prefix
# Format: {path_prefix: {"read": [...], "write": [...]}}
# Values can be: role names, "team:{team}", agent slugs, or "*" for all
DOCS_PERMISSIONS: Final[dict[str, dict[str, list[str]]]] = {
    # CEO-only
    "internal": {
        "read": ["ceo"],
        "write": ["ceo"],
    },
    # Standards - all read, PM/Documenter write (for proposals)
    "standards": {
        "read": [ALL_AGENTS],
        "write": ["main_pm", "cell_pm", "documenter"],
    },
    # Workflows - all read, Main PM write
    "workflows": {
        "read": [ALL_AGENTS],
        "write": ["main_pm"],
    },
    # Team docs - team reads, documenter writes
    "backend": {
        "read": ["team:backend", "main_pm", "cell_pm"],
        "write": ["be-doc"],
    },
    "frontend": {
        "read": ["team:frontend", "main_pm", "cell_pm"],
        "write": ["fe-doc"],
    },
    "ux_ui": {
        "read": ["team:ux_ui", "main_pm", "cell_pm"],
        "write": ["ux-doc"],
    },
    # Features - team-scoped
    "features/backend": {
        "read": ["team:backend", "main_pm", "cell_pm"],
        "write": ["be-doc"],
    },
    "features/frontend": {
        "read": ["team:frontend", "main_pm", "cell_pm"],
        "write": ["fe-doc"],
    },
    "features/ux_ui": {
        "read": ["team:ux_ui", "main_pm", "cell_pm"],
        "write": ["ux-doc"],
    },
    "features/shared": {
        "read": [ALL_AGENTS],
        "write": ALL_DOCS,
    },
    # Bugs - all read, team documenters write
    "bugs/backend": {
        "read": [ALL_AGENTS],
        "write": ["be-doc"],
    },
    "bugs/frontend": {
        "read": [ALL_AGENTS],
        "write": ["fe-doc"],
    },
    "bugs/ux_ui": {
        "read": [ALL_AGENTS],
        "write": ["ux-doc"],
    },
    "bugs/resolved": {
        "read": [ALL_AGENTS],
        "write": ALL_DOCS,
    },
    # Self docs - Board/PM read, Main PM/Auditor write
    "self": {
        "read": ["main_pm", "cell_pm", "auditor", "product_owner"],
        "write": ["main_pm", "auditor"],
    },
    # Initiatives - default to PM access (specific initiatives override)
    "initiatives": {
        "read": ["main_pm", "cell_pm", "product_owner"],
        "write": ["main_pm"],
    },
}


# =============================================================================
# PERMISSION CHECK FUNCTIONS
# =============================================================================


def _strip_path_prefixes(path: str) -> str:
    """Strip leading slashes and ``app/`` / ``docs/`` prefixes."""
    path = path.lstrip("/")
    if path.startswith("app/"):
        path = path[4:]
    if path.startswith("docs/"):
        path = path[5:]
    return path


def _normalize_path(path: str) -> str:
    """Normalize docs path for permission checking.

    Handles paths like:
    - /app/docs/backend/api/README.md -> backend
    - docs/standards/coding/python.md -> standards
    - /docs/features/shared/foo.md -> features/shared
    """
    path = _strip_path_prefixes(path)
    if not path:
        return ""

    parts = path.split("/")
    if len(parts) >= MIN_NESTED_PATH_PARTS and parts[0] in ("features", "bugs"):
        return f"{parts[0]}/{parts[1]}"

    return parts[0]


def _get_permission_rule(path_prefix: str) -> dict[str, list[str]] | None:
    """Get permission rule for a path prefix."""
    # Try exact match first
    if path_prefix in DOCS_PERMISSIONS:
        return DOCS_PERMISSIONS[path_prefix]

    # Try parent paths
    for key in DOCS_PERMISSIONS:
        if path_prefix.startswith(key):
            return DOCS_PERMISSIONS[key]

    return None


def _agent_matches_permission(
    agent_slug: str, agent_role: str, agent_team: str | None, permission: str
) -> bool:
    """Check if an agent matches a permission entry."""
    # Wildcard - everyone matches
    if permission == ALL_AGENTS:
        return True

    # Direct slug match (e.g., "be-doc")
    if permission == agent_slug:
        return True

    # Role match (e.g., "main_pm", "developer")
    if permission == agent_role:
        return True

    # Team match (e.g., "team:backend")
    if permission.startswith("team:"):
        team = permission[5:]  # Remove "team:" prefix
        return agent_team == team

    return False


def _fast_path_access_decision(
    role: str | None, path_prefix: str, action: str
) -> bool | None:
    """Apply fast-path rules before the per-path permission lookup.

    Returns a definitive True/False when the decision is trivial, else None
    so the caller falls through to per-rule matching.
    """
    if not role:
        return False
    if role in FULL_ACCESS_ROLES:
        return True
    if not path_prefix or path_prefix == "internal":
        return False
    if role in READ_ALL_ROLES and action == "read":
        return True
    return None


def check_docs_access(agent_id: str, path: str, action: str) -> bool:
    """
    Check if an agent can access a docs path.

    Args:
        agent_id: Agent slug or UUID
        path: File path to check (can include /app/docs prefix)
        action: "read" or "write"

    Returns:
        True if access is allowed, False otherwise
    """
    slug = _resolve_to_slug(agent_id)
    role = AGENT_ROLE_MAP.get(slug)
    team = AGENT_TEAM_MAP.get(slug)

    if not role:
        logger.warning("Unknown agent for docs access check", agent_id=agent_id)
        return False

    path_prefix = _normalize_path(path)
    fast = _fast_path_access_decision(role, path_prefix, action)
    if fast is not None:
        return fast

    rule = _get_permission_rule(path_prefix)
    if not rule:
        logger.warning("No permission rule for path", path=path, prefix=path_prefix)
        return False

    return _check_permission_match(slug, role, team, rule.get(action, []))


def _check_permission_match(
    slug: str, role: str, team: str | None, allowed: list[str] | str
) -> bool:
    """Check if an agent matches any permission in the allowed list."""
    if isinstance(allowed, str):
        return _agent_matches_permission(slug, role, team, allowed)

    return any(_agent_matches_permission(slug, role, team, perm) for perm in allowed)


def require_docs_access(agent_id: str, path: str, action: str) -> None:
    """
    Require docs access or raise PermissionDeniedError.

    Args:
        agent_id: Agent slug or UUID
        path: File path to check
        action: "read" or "write"

    Raises:
        PermissionDeniedError: If access is not allowed
    """
    if not check_docs_access(agent_id, path, action):
        slug = _resolve_to_slug(agent_id)
        raise PermissionDeniedError(
            f"Agent {slug} cannot {action} {path}",
            details={
                "agent_id": agent_id,
                "path": path,
                "action": action,
            },
        )


def _path_allowed_for_agent(
    rule: dict[str, list[str]],
    slug: str,
    role: str,
    team: str | None,
    action: str,
) -> bool:
    """Return True if ``action`` is permitted on a single path rule."""
    if role in READ_ALL_ROLES and action == "read":
        return True
    perms = rule.get(action, [])
    return any(_agent_matches_permission(slug, role, team, perm) for perm in perms)


def get_allowed_docs_paths(agent_id: str, action: str = "read") -> list[str]:
    """
    Get list of docs paths an agent can access.

    Args:
        agent_id: Agent slug or UUID
        action: "read" or "write"

    Returns:
        List of path prefixes the agent can access
    """
    slug = _resolve_to_slug(agent_id)
    role = AGENT_ROLE_MAP.get(slug)
    team = AGENT_TEAM_MAP.get(slug)

    if not role:
        return []

    if role in FULL_ACCESS_ROLES:
        return list(DOCS_PERMISSIONS.keys())

    return [
        path_prefix
        for path_prefix, rule in DOCS_PERMISSIONS.items()
        if path_prefix != "internal"
        and _path_allowed_for_agent(rule, slug, role, team, action)
    ]
