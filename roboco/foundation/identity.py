"""Identity foundation — single source for roles, teams, agents, role-sets.

Replaces the parallel definitions across:
  - models/base.py (AgentRole, Team)
  - lifecycle/spec.py (Role)
  - agents_config.py (AGENT_ROLE_MAP, AGENT_TEAM_MAP, CELL_MEMBERS, PM_ROLES,
    _BOARD_ROLES)
  - seeds/initial_data.py (AGENT_UUIDS, DEFAULT_AGENTS slug+role+team fields)
  - services/permissions.py (PM_ROLES — 2-role variant)
  - runtime/orchestrator.py (_AGENT_TEAM_MAP + cell-prefix table)

Every consumer imports from here. Adding an agent edits exactly this file.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum, StrEnum
from uuid import UUID


class Role(StrEnum):
    DEVELOPER = "developer"
    QA = "qa"
    DOCUMENTER = "documenter"
    CELL_PM = "cell_pm"
    MAIN_PM = "main_pm"
    PRODUCT_OWNER = "product_owner"
    HEAD_MARKETING = "head_marketing"
    AUDITOR = "auditor"
    PR_REVIEWER = "pr_reviewer"  # reviews PRs (inbound external/fork PRs first)
    PROMPTER = "prompter"  # intake interviewer — talks only to the human, drafts tasks
    SECRETARY = "secretary"  # CEO's chief-of-staff — acts only under CEO command
    CEO = "ceo"
    SYSTEM = "system"  # sentinel only — used for orchestrator-generated rows


class Team(StrEnum):
    BACKEND = "backend"
    FRONTEND = "frontend"
    UX_UI = "ux_ui"
    BOARD = "board"
    MAIN_PM = "main_pm"
    FULLSTACK = "fullstack"
    MARKETING = "marketing"  # legacy — kept for seed-data parity, no agent declares it
    SYSTEM = "system"


# The three delivery cells. A Product maps one Project per cell. This is the
# single source of truth for "the cells" subset — distinct from the full Team
# enum (which also has board/main_pm/marketing/system). Replaces the prior
# string-literal duplicates in agents_config.CELL_MEMBERS and
# orchestrator.cell_teams.
CELL_TEAMS: frozenset[Team] = frozenset({Team.BACKEND, Team.FRONTEND, Team.UX_UI})


class RoleLevel(IntEnum):
    SYSTEM = -1
    INTAKE = 0  # read-only intake interviewer — lowest real-agent authority
    DEV = 1
    QA = 2
    DOCUMENTER = 3
    CELL_PM = 4
    MAIN_PM = 5
    BOARD = 6
    AUDITOR = 7  # observer override — sees everything
    CEO = 8


@dataclass(frozen=True)
class AgentRow:
    """Identity record for one agent. Single source of truth."""

    slug: str
    role: Role
    team: Team
    uuid: UUID
    is_human: bool = False  # True only for ceo


def _u(s: str) -> UUID:
    """Shorthand for UUID literals in the AGENTS table."""
    return UUID(s)


AGENTS: dict[str, AgentRow] = {
    # System sentinel — used as from_agent for orchestrator-generated rows.
    "system": AgentRow(
        "system", Role.SYSTEM, Team.SYSTEM, _u("00000000-0000-0000-0000-000000000000")
    ),
    # CEO (Human)
    "ceo": AgentRow(
        "ceo",
        Role.CEO,
        Team.BOARD,
        _u("00000000-0000-0000-0000-000000000001"),
        is_human=True,
    ),
    # Backend cell
    "be-dev-1": AgentRow(
        "be-dev-1",
        Role.DEVELOPER,
        Team.BACKEND,
        _u("00000000-0000-0000-0001-000000000001"),
    ),
    "be-dev-2": AgentRow(
        "be-dev-2",
        Role.DEVELOPER,
        Team.BACKEND,
        _u("00000000-0000-0000-0001-000000000002"),
    ),
    "be-qa": AgentRow(
        "be-qa", Role.QA, Team.BACKEND, _u("00000000-0000-0000-0001-000000000003")
    ),
    "be-pm": AgentRow(
        "be-pm", Role.CELL_PM, Team.BACKEND, _u("00000000-0000-0000-0001-000000000004")
    ),
    "be-doc": AgentRow(
        "be-doc",
        Role.DOCUMENTER,
        Team.BACKEND,
        _u("00000000-0000-0000-0001-000000000005"),
    ),
    # Frontend cell
    "fe-dev-1": AgentRow(
        "fe-dev-1",
        Role.DEVELOPER,
        Team.FRONTEND,
        _u("00000000-0000-0000-0002-000000000001"),
    ),
    "fe-dev-2": AgentRow(
        "fe-dev-2",
        Role.DEVELOPER,
        Team.FRONTEND,
        _u("00000000-0000-0000-0002-000000000002"),
    ),
    "fe-qa": AgentRow(
        "fe-qa", Role.QA, Team.FRONTEND, _u("00000000-0000-0000-0002-000000000003")
    ),
    "fe-pm": AgentRow(
        "fe-pm", Role.CELL_PM, Team.FRONTEND, _u("00000000-0000-0000-0002-000000000004")
    ),
    "fe-doc": AgentRow(
        "fe-doc",
        Role.DOCUMENTER,
        Team.FRONTEND,
        _u("00000000-0000-0000-0002-000000000005"),
    ),
    # UX/UI cell
    "ux-dev-1": AgentRow(
        "ux-dev-1",
        Role.DEVELOPER,
        Team.UX_UI,
        _u("00000000-0000-0000-0003-000000000001"),
    ),
    "ux-dev-2": AgentRow(
        "ux-dev-2",
        Role.DEVELOPER,
        Team.UX_UI,
        _u("00000000-0000-0000-0003-000000000002"),
    ),
    "ux-qa": AgentRow(
        "ux-qa", Role.QA, Team.UX_UI, _u("00000000-0000-0000-0003-000000000003")
    ),
    "ux-pm": AgentRow(
        "ux-pm", Role.CELL_PM, Team.UX_UI, _u("00000000-0000-0000-0003-000000000004")
    ),
    "ux-doc": AgentRow(
        "ux-doc",
        Role.DOCUMENTER,
        Team.UX_UI,
        _u("00000000-0000-0000-0003-000000000005"),
    ),
    # Board / Management
    "main-pm": AgentRow(
        "main-pm",
        Role.MAIN_PM,
        Team.MAIN_PM,
        _u("00000000-0000-0000-0004-000000000001"),
    ),
    "product-owner": AgentRow(
        "product-owner",
        Role.PRODUCT_OWNER,
        Team.BOARD,
        _u("00000000-0000-0000-0004-000000000002"),
    ),
    "head-marketing": AgentRow(
        "head-marketing",
        Role.HEAD_MARKETING,
        Team.BOARD,
        _u("00000000-0000-0000-0004-000000000003"),
    ),
    "auditor": AgentRow(
        "auditor", Role.AUDITOR, Team.BOARD, _u("00000000-0000-0000-0004-000000000004")
    ),
    # Intake interviewer — CEO-adjacent (board team), but NOT a board reviewer
    # (deliberately absent from BOARD_ROLES). Spawned on demand to chat with the
    # human and draft a task; talks to no other agent.
    "intake-1": AgentRow(
        "intake-1",
        Role.PROMPTER,
        Team.BOARD,
        _u("00000000-0000-0000-0004-000000000005"),
    ),
    # Secretary — the CEO's conversational chief-of-staff. Like intake, a single
    # seeded, board-adjacent agent the human chats with; unlike intake it carries
    # gated CEO authority and acts only under CEO command. Deliberately absent
    # from BOARD_ROLES (not a board reviewer).
    "secretary-1": AgentRow(
        "secretary-1",
        Role.SECRETARY,
        Team.BOARD,
        _u("00000000-0000-0000-0004-000000000006"),
    ),
    # PR reviewer — a single global, read-only agent. Reviews PRs (inbound
    # external/fork PRs first) and posts one change-request; never writes code.
    # Like the auditor it is board-team and read-only, but it DOES act on review
    # tasks.
    "pr-reviewer-1": AgentRow(
        "pr-reviewer-1",
        Role.PR_REVIEWER,
        Team.BOARD,
        _u("00000000-0000-0000-0004-000000000007"),
    ),
}


# Role-sets (frozensets so they're hashable + immutable)
PM_ROLES: frozenset[Role] = frozenset({Role.CELL_PM, Role.MAIN_PM})
BOARD_ROLES: frozenset[Role] = frozenset(
    {Role.PRODUCT_OWNER, Role.HEAD_MARKETING, Role.AUDITOR}
)
DEV_ROLES: frozenset[Role] = frozenset({Role.DEVELOPER})
REVIEWER_ROLES: frozenset[Role] = frozenset({Role.PR_REVIEWER})
ALL_ROLES: frozenset[Role] = frozenset(Role)

# Hierarchical level for "X or above" checks. SYSTEM is the sentinel below all
# real roles. Auditor sits above main_pm because the auditor can read
# everywhere; CEO is the absolute ceiling.
ROLE_LEVEL: dict[Role, RoleLevel] = {
    Role.SYSTEM: RoleLevel.SYSTEM,
    Role.DEVELOPER: RoleLevel.DEV,
    Role.QA: RoleLevel.QA,
    Role.DOCUMENTER: RoleLevel.DOCUMENTER,
    Role.CELL_PM: RoleLevel.CELL_PM,
    Role.MAIN_PM: RoleLevel.MAIN_PM,
    Role.PRODUCT_OWNER: RoleLevel.BOARD,
    Role.HEAD_MARKETING: RoleLevel.BOARD,
    Role.AUDITOR: RoleLevel.AUDITOR,
    Role.PR_REVIEWER: RoleLevel.QA,  # a code reviewer, peer to QA in authority
    Role.PROMPTER: RoleLevel.INTAKE,
    Role.SECRETARY: RoleLevel.BOARD,
    Role.CEO: RoleLevel.CEO,
}


def agent_for_slug(slug: str) -> AgentRow:
    """Return the AgentRow for a slug; raises KeyError on unknown."""
    if slug not in AGENTS:
        raise KeyError(f"unknown agent slug: {slug!r} (known: {sorted(AGENTS)})")
    return AGENTS[slug]


def slugs_for_role(role: Role) -> frozenset[str]:
    """Return the frozenset of slugs whose agent has this role."""
    return frozenset(slug for slug, row in AGENTS.items() if row.role == role)


def slugs_for_team(team: Team) -> frozenset[str]:
    """Return the frozenset of slugs whose agent is on this team."""
    return frozenset(slug for slug, row in AGENTS.items() if row.team == team)


def role_for_slug(slug: str) -> Role:
    """Shorthand for `agent_for_slug(slug).role`."""
    return agent_for_slug(slug).role


def team_for_slug(slug: str) -> Team:
    """Shorthand for `agent_for_slug(slug).team`."""
    return agent_for_slug(slug).team
