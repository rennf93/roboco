"""
Initial Data Constants

Static seed data for bootstrapping the RoboCo system.
Separates data definitions from bootstrap logic.
"""

from typing import Any

from roboco.foundation import identity as _foundation
from roboco.foundation.policy import communications as _comms

# =============================================================================
# DEFAULT CHANNELS
#
# Channel topology (slug, description, type, membership) is canonicalized in
# `roboco.foundation.policy.communications.CHANNELS`. The DEFAULT_CHANNELS
# list and CHANNEL_MEMBERSHIPS dict below derive from that catalog at module
# load. The only seed-only field is `name` — a presentation string the
# foundation does not (and should not) own. It lives in
# `_CHANNEL_PRESENTATION` and is the only edit needed to rename a channel
# label.
# =============================================================================

# Per-channel display name. Slug, description, type, and membership are all
# sourced from foundation.CHANNELS; this dict only carries presentation.
_CHANNEL_PRESENTATION: dict[str, str] = {
    "backend-cell": "Backend Cell",
    "frontend-cell": "Frontend Cell",
    "uxui-cell": "UX/UI Cell",
    "dev-all": "All Developers",
    "qa-all": "All QA",
    "pm-all": "All PMs",
    "doc-all": "All Documenters",
    "main-pm-board": "Main PM & Board",
    "board-private": "Board Private",
    "announcements": "Announcements",
    "all-hands": "All Hands",
}


def _build_default_channels() -> list[dict[str, Any]]:
    """Compose DEFAULT_CHANNELS rows from foundation specs + display names."""
    return [
        {
            "slug": spec.slug,
            "name": _CHANNEL_PRESENTATION[slug],
            "description": spec.description,
            # Legacy DB seeder keys this as `channel_type`; preserve the name
            # so create_channels(session) keeps working unchanged.
            "channel_type": spec.type.value,
        }
        for slug, spec in _comms.CHANNELS.items()
    ]


DEFAULT_CHANNELS: list[dict[str, Any]] = _build_default_channels()


# =============================================================================
# DEFAULT AGENTS
#
# All agents have static UUIDs for consistent mapping between:
# - Database records
# - Task assignments
# - Container orchestration
#
# UUID scheme (encoded in roboco/foundation/identity.py:AGENTS):
# - 0000-0000: System sentinel + CEO (human)
# - 0001-000X: Backend cell
# - 0002-000X: Frontend cell
# - 0003-000X: UX/UI cell
# - 0004-000X: Board/Management
#
# UUIDs, role, and team are all sourced from foundation.AGENTS so that
# adding/renaming an agent is a single-file edit. Per-agent presentation
# (display name) lives below in _AGENT_PRESENTATION because it is the
# only field foundation does not (and should not) own.
# =============================================================================

# Derived AGENT_UUIDS — string-keyed for backward compat with consumers
# that index by slug and read string-typed UUIDs.
AGENT_UUIDS: dict[str, str] = {
    slug: str(row.uuid) for slug, row in _foundation.AGENTS.items()
}

# Per-agent display names. Anything role/team/uuid is sourced from
# foundation; this dict only carries presentation strings.
_AGENT_PRESENTATION: dict[str, dict[str, Any]] = {
    "ceo": {"name": "Renzo"},
    "be-dev-1": {"name": "Backend Developer 1"},
    "be-dev-2": {"name": "Backend Developer 2"},
    "be-qa": {"name": "Backend QA"},
    "be-pm": {"name": "Backend PM"},
    "be-doc": {"name": "Backend Documenter"},
    "fe-dev-1": {"name": "Frontend Developer 1"},
    "fe-dev-2": {"name": "Frontend Developer 2"},
    "fe-qa": {"name": "Frontend QA"},
    "fe-pm": {"name": "Frontend PM"},
    "fe-doc": {"name": "Frontend Documenter"},
    "ux-dev-1": {"name": "UX/UI Developer 1"},
    "ux-dev-2": {"name": "UX/UI Developer 2"},
    "ux-qa": {"name": "UX/UI QA"},
    "ux-pm": {"name": "UX/UI PM"},
    "ux-doc": {"name": "UX/UI Documenter"},
    "main-pm": {"name": "Main PM"},
    "product-owner": {"name": "Product Owner"},
    "head-marketing": {"name": "Head of Marketing"},
    "auditor": {"name": "Auditor"},
    "intake-1": {"name": "Intake"},
    "secretary-1": {"name": "Secretary"},
}


def _build_default_agents() -> list[dict[str, Any]]:
    """Compose DEFAULT_AGENTS rows from foundation + presentation metadata.

    The system sentinel is appended as a literal because:
      1. The postgres `team` enum does not include 'system' — only
         'backend|frontend|ux_ui|board|main_pm|fullstack|marketing'.
         Seeding with team='system' would fail at INSERT.
      2. The system row is a from_agent FK target, never a participant.
    """
    rows: list[dict[str, Any]] = []
    for slug, row in _foundation.AGENTS.items():
        if slug == "system":
            continue
        rows.append(
            {
                "id": str(row.uuid),
                "slug": slug,
                "role": row.role.value,
                "team": row.team.value,
                **_AGENT_PRESENTATION[slug],
            }
        )
    # System sentinel — kept as a literal so we can pass team=None into the
    # DB without colliding with the postgres `team` enum (which does not
    # have a 'system' value).
    rows.append(
        {
            "id": str(_foundation.AGENTS["system"].uuid),
            "slug": "system",
            "name": "System",
            "role": _foundation.AGENTS["system"].role.value,
            "team": None,
        }
    )
    return rows


DEFAULT_AGENTS: list[dict[str, Any]] = _build_default_agents()


# =============================================================================
# CHANNEL MEMBERSHIP
#
# Populates the database channel.members / channel.writers / silent_observers
# fields at seed time. Membership is now derived from
# foundation.policy.communications.CHANNELS — adding/removing an agent from a
# channel is a single edit in the foundation catalog.
#
# NOTE: This is SEPARATE from roboco/agents_config.py CHANNEL_ACCESS which is
# the runtime permission source of truth. Both derive from the same
# foundation catalog. Privileged roles (CEO, Auditor, Main PM) still bypass
# membership at runtime via has_privileged_access() in services/permissions.py.
# =============================================================================

CEO_AGENT_ID = AGENT_UUIDS["ceo"]

# Cell-member roles subject to a channel's team_scope. Cross-cell roles
# (MAIN_PM, AUDITOR, CEO, board) are NOT filtered by team_scope. Mirrors the
# rule in agents_config._TEAM_SCOPED_ROLES; duplicated here to avoid a
# circular import (agents_config already imports AGENT_UUIDS from this
# module).
_TEAM_SCOPED_ROLES: frozenset[_foundation.Role] = frozenset(
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


def _build_channel_memberships() -> dict[str, list[str]]:
    """Per-channel sorted member slugs derived from foundation.CHANNELS."""
    return {
        slug: _slugs_for_role_set(spec.read_roles, spec.team_scope)
        for slug, spec in _comms.CHANNELS.items()
    }


CHANNEL_MEMBERSHIPS: dict[str, list[str]] = _build_channel_memberships()


# Auditor silent-read channels — derived from CHANNELS where AUDITOR appears
# in silent_roles.
AUDITOR_SILENT_ACCESS: list[str] = sorted(
    slug
    for slug, spec in _comms.CHANNELS.items()
    if _foundation.Role.AUDITOR in spec.silent_roles
)


# =============================================================================
# INITIAL CHANNEL MESSAGES
# =============================================================================

INITIAL_MESSAGES = {
    "announcements": {
        "agent_id": "main-pm",
        "content": """Welcome to RoboCo!

This is the official announcements channel. Company-wide updates will be posted here.

**Key Channels:**
- `#backend-cell`, `#frontend-cell`, `#uxui-cell` - Team communication
- `#dev-all`, `#qa-all`, `#pm-all`, `#doc-all` - Cross-cell role channels
- `#all-hands` - Company-wide open discussion

**Workflow:**
1. Call `give_me_work()` to receive your next assignment
2. Claim tasks in your team
3. Follow the lifecycle: CLAIM -> IN_PROGRESS -> VERIFY -> QA -> DOCS -> COMPLETE
4. Use your journal to track learning and decisions

Let's build something great together!
""",
    },
    "all-hands": {
        "agent_id": "main-pm",
        "content": """This is the all-hands channel for company-wide discussions.

Feel free to:
- Ask questions that span multiple teams
- Share interesting findings
- Discuss architecture decisions that affect everyone
- Celebrate wins and completed tasks

Please keep cell-specific discussions in your respective cell channels.
""",
    },
    "backend-cell": {
        "agent_id": "be-pm",
        "content": """Welcome to the Backend Cell channel!

**Team:**
- be-dev-1, be-dev-2: Backend Developers
- be-qa: Backend QA
- be-pm: Backend PM (me)
- be-doc: Backend Documenter

**Our Focus:**
- API development
- Database design
- Service architecture
- Performance optimization

Call `give_me_work()` to receive your next backend task.
""",
    },
    "frontend-cell": {
        "agent_id": "fe-pm",
        "content": """Welcome to the Frontend Cell channel!

**Team:**
- fe-dev-1, fe-dev-2: Frontend Developers
- fe-qa: Frontend QA
- fe-pm: Frontend PM (me)
- fe-doc: Frontend Documenter

**Our Focus:**
- UI development
- User experience
- Component architecture
- State management

Call `give_me_work()` to receive your next frontend task.
""",
    },
    "uxui-cell": {
        "agent_id": "ux-pm",
        "content": """Welcome to the UX/UI Cell channel!

**Team:**
- ux-dev-1, ux-dev-2: UX/UI Developers
- ux-qa: UX/UI QA
- ux-pm: UX/UI PM (me)
- ux-doc: UX/UI Documenter

**Our Focus:**
- Design systems
- User research
- Prototyping
- Accessibility

Call `give_me_work()` to receive your next UX/UI task.
""",
    },
}
