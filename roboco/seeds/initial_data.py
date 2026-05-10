"""
Initial Data Constants

Static seed data for bootstrapping the RoboCo system.
Separates data definitions from bootstrap logic.
"""

from typing import Any

from roboco.foundation import identity as _foundation

# =============================================================================
# DEFAULT CHANNELS
# =============================================================================

DEFAULT_CHANNELS = [
    # Cell channels
    {
        "slug": "backend-cell",
        "name": "Backend Cell",
        "description": "Backend development team channel",
        "channel_type": "cell",
    },
    {
        "slug": "frontend-cell",
        "name": "Frontend Cell",
        "description": "Frontend development team channel",
        "channel_type": "cell",
    },
    {
        "slug": "uxui-cell",
        "name": "UX/UI Cell",
        "description": "UX/UI design team channel",
        "channel_type": "cell",
    },
    # Cross-cell role channels
    {
        "slug": "dev-all",
        "name": "All Developers",
        "description": "Cross-cell developer discussion",
        "channel_type": "cross_cell",
    },
    {
        "slug": "qa-all",
        "name": "All QA",
        "description": "Cross-cell QA discussion",
        "channel_type": "cross_cell",
    },
    {
        "slug": "pm-all",
        "name": "All PMs",
        "description": "Cross-cell PM coordination",
        "channel_type": "cross_cell",
    },
    {
        "slug": "doc-all",
        "name": "All Documenters",
        "description": "Cross-cell documentation discussion",
        "channel_type": "cross_cell",
    },
    # Management channels
    {
        "slug": "main-pm-board",
        "name": "Main PM & Board",
        "description": "Main PM and Board communication",
        "channel_type": "management",
    },
    {
        "slug": "board-private",
        "name": "Board Private",
        "description": "Board-only discussions",
        "channel_type": "management",
    },
    # Special channels
    {
        "slug": "announcements",
        "name": "Announcements",
        "description": "Company-wide announcements (read-only for most)",
        "channel_type": "special",
    },
    {
        "slug": "all-hands",
        "name": "All Hands",
        "description": "Company-wide open discussion",
        "channel_type": "special",
    },
]


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
# This populates the database channel.members/writers fields for initial setup.
#
# NOTE: This is SEPARATE from roboco/agents_config.py CHANNEL_ACCESS which is
# the runtime permission source of truth. The relationship is:
#
# 1. CHANNEL_MEMBERSHIPS (here) -> populates database channel.members
# 2. CHANNEL_ACCESS (agents_config) -> used by PermissionService for checks
# 3. Privileged roles (CEO, Auditor, Main PM) bypass membership via
#    has_privileged_access() in services/permissions.py
#
# This means main-pm isn't listed in board-private here but CAN read it
# via the privileged role bypass. The seed data is for UI/listing purposes,
# while CHANNEL_ACCESS is the actual permission enforcement.
# =============================================================================

CEO_AGENT_ID = AGENT_UUIDS["ceo"]

CHANNEL_MEMBERSHIPS = {
    # Cell channels - cell members + CEO
    "backend-cell": ["be-dev-1", "be-dev-2", "be-qa", "be-pm", "be-doc", "ceo"],
    "frontend-cell": ["fe-dev-1", "fe-dev-2", "fe-qa", "fe-pm", "fe-doc", "ceo"],
    "uxui-cell": ["ux-dev-1", "ux-dev-2", "ux-qa", "ux-pm", "ux-doc", "ceo"],
    # Role channels + CEO
    "dev-all": [
        "be-dev-1",
        "be-dev-2",
        "fe-dev-1",
        "fe-dev-2",
        "ux-dev-1",
        "ux-dev-2",
        "ceo",
    ],
    "qa-all": ["be-qa", "fe-qa", "ux-qa", "ceo"],
    "pm-all": ["be-pm", "fe-pm", "ux-pm", "main-pm", "ceo"],
    "doc-all": ["be-doc", "fe-doc", "ux-doc", "ceo"],
    # Management channels + CEO
    "main-pm-board": [
        "main-pm",
        "product-owner",
        "head-marketing",
        "auditor",
        "ceo",
    ],
    "board-private": ["product-owner", "head-marketing", "auditor", "ceo"],
    # Broadcast channels - everyone human-or-agent (system sentinel
    # excluded — it's a from_agent placeholder, not a participant).
    "announcements": [a["slug"] for a in DEFAULT_AGENTS if a["slug"] != "system"],
    "all-hands": [a["slug"] for a in DEFAULT_AGENTS if a["slug"] != "system"],
}

# Auditor has silent read access to cell/role channels
AUDITOR_SILENT_ACCESS = [
    "backend-cell",
    "frontend-cell",
    "uxui-cell",
    "dev-all",
    "qa-all",
    "pm-all",
    "doc-all",
]


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
1. Check `roboco_task_scan()` for pending work
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

Check `roboco_task_scan(team="backend")` for pending backend tasks.
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

Check `roboco_task_scan(team="frontend")` for pending frontend tasks.
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

Check `roboco_task_scan(team="ux_ui")` for pending UX/UI tasks.
""",
    },
}
