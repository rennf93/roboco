"""RoboCo foundation package — single source of truth for cross-cutting policy.

Sub-packages:
  - identity: Role, Team, AgentRow, AGENTS, role-sets, lookups
  - policy: per-domain policy modules (task_completeness, ...)

See docs/superpowers/specs/2026-05-10-foundation-canonicalization-design.md.
"""

from roboco.foundation.identity import (
    AGENTS,
    ALL_ROLES,
    BOARD_ROLES,
    DEV_ROLES,
    PM_ROLES,
    ROLE_LEVEL,
    AgentRow,
    Role,
    RoleLevel,
    Team,
    agent_for_slug,
    role_for_slug,
    slugs_for_role,
    slugs_for_team,
    team_for_slug,
)

__all__ = [
    "AGENTS",
    "ALL_ROLES",
    "BOARD_ROLES",
    "DEV_ROLES",
    "PM_ROLES",
    "ROLE_LEVEL",
    "AgentRow",
    "Role",
    "RoleLevel",
    "Team",
    "agent_for_slug",
    "role_for_slug",
    "slugs_for_role",
    "slugs_for_team",
    "team_for_slug",
]
