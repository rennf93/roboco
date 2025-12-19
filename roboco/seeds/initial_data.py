"""
Initial Data Constants

Static seed data for bootstrapping the RoboCo system.
Separates data definitions from bootstrap logic.
"""

from typing import Any

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
# UUID scheme:
# - 0000-0000: CEO (human)
# - 0001-000X: Backend cell
# - 0002-000X: Frontend cell
# - 0003-000X: UX/UI cell
# - 0004-000X: Board/Management
# =============================================================================

# Static agent UUIDs - NEVER change these after initial deployment
AGENT_UUIDS = {
    # CEO (Human)
    "ceo": "00000000-0000-0000-0000-000000000001",
    # Backend Cell
    "be-dev-1": "00000000-0000-0000-0001-000000000001",
    "be-dev-2": "00000000-0000-0000-0001-000000000002",
    "be-qa": "00000000-0000-0000-0001-000000000003",
    "be-pm": "00000000-0000-0000-0001-000000000004",
    "be-doc": "00000000-0000-0000-0001-000000000005",
    # Frontend Cell
    "fe-dev-1": "00000000-0000-0000-0002-000000000001",
    "fe-dev-2": "00000000-0000-0000-0002-000000000002",
    "fe-qa": "00000000-0000-0000-0002-000000000003",
    "fe-pm": "00000000-0000-0000-0002-000000000004",
    "fe-doc": "00000000-0000-0000-0002-000000000005",
    # UX/UI Cell
    "ux-dev": "00000000-0000-0000-0003-000000000001",
    "ux-qa": "00000000-0000-0000-0003-000000000002",
    "ux-pm": "00000000-0000-0000-0003-000000000003",
    "ux-doc": "00000000-0000-0000-0003-000000000004",
    # Board / Management
    "main-pm": "00000000-0000-0000-0004-000000000001",
    "product-owner": "00000000-0000-0000-0004-000000000002",
    "head-marketing": "00000000-0000-0000-0004-000000000003",
    "auditor": "00000000-0000-0000-0004-000000000004",
}

DEFAULT_AGENTS: list[dict[str, Any]] = [
    # Backend Cell
    {
        "id": AGENT_UUIDS["be-dev-1"],
        "slug": "be-dev-1",
        "name": "Backend Developer 1",
        "role": "developer",
        "team": "backend",
    },
    {
        "id": AGENT_UUIDS["be-dev-2"],
        "slug": "be-dev-2",
        "name": "Backend Developer 2",
        "role": "developer",
        "team": "backend",
    },
    {
        "id": AGENT_UUIDS["be-qa"],
        "slug": "be-qa",
        "name": "Backend QA",
        "role": "qa",
        "team": "backend",
    },
    {
        "id": AGENT_UUIDS["be-pm"],
        "slug": "be-pm",
        "name": "Backend PM",
        "role": "cell_pm",
        "team": "backend",
    },
    {
        "id": AGENT_UUIDS["be-doc"],
        "slug": "be-doc",
        "name": "Backend Documenter",
        "role": "documenter",
        "team": "backend",
    },
    # Frontend Cell
    {
        "id": AGENT_UUIDS["fe-dev-1"],
        "slug": "fe-dev-1",
        "name": "Frontend Developer 1",
        "role": "developer",
        "team": "frontend",
    },
    {
        "id": AGENT_UUIDS["fe-dev-2"],
        "slug": "fe-dev-2",
        "name": "Frontend Developer 2",
        "role": "developer",
        "team": "frontend",
    },
    {
        "id": AGENT_UUIDS["fe-qa"],
        "slug": "fe-qa",
        "name": "Frontend QA",
        "role": "qa",
        "team": "frontend",
    },
    {
        "id": AGENT_UUIDS["fe-pm"],
        "slug": "fe-pm",
        "name": "Frontend PM",
        "role": "cell_pm",
        "team": "frontend",
    },
    {
        "id": AGENT_UUIDS["fe-doc"],
        "slug": "fe-doc",
        "name": "Frontend Documenter",
        "role": "documenter",
        "team": "frontend",
    },
    # UX/UI Cell
    {
        "id": AGENT_UUIDS["ux-dev"],
        "slug": "ux-dev",
        "name": "UX/UI Developer",
        "role": "developer",
        "team": "ux_ui",
    },
    {
        "id": AGENT_UUIDS["ux-qa"],
        "slug": "ux-qa",
        "name": "UX/UI QA",
        "role": "qa",
        "team": "ux_ui",
    },
    {
        "id": AGENT_UUIDS["ux-pm"],
        "slug": "ux-pm",
        "name": "UX/UI PM",
        "role": "cell_pm",
        "team": "ux_ui",
    },
    {
        "id": AGENT_UUIDS["ux-doc"],
        "slug": "ux-doc",
        "name": "UX/UI Documenter",
        "role": "documenter",
        "team": "ux_ui",
    },
    # Board / Management
    {
        "id": AGENT_UUIDS["main-pm"],
        "slug": "main-pm",
        "name": "Main PM",
        "role": "main_pm",
        "team": None,
    },
    {
        "id": AGENT_UUIDS["product-owner"],
        "slug": "product-owner",
        "name": "Product Owner",
        "role": "product_owner",
        "team": None,
    },
    {
        "id": AGENT_UUIDS["head-marketing"],
        "slug": "head-marketing",
        "name": "Head of Marketing",
        "role": "head_marketing",
        "team": None,
    },
    {
        "id": AGENT_UUIDS["auditor"],
        "slug": "auditor",
        "name": "Auditor",
        "role": "auditor",
        "team": None,
    },
    # CEO (Human)
    {
        "id": AGENT_UUIDS["ceo"],
        "slug": "ceo",
        "name": "Renzo",
        "role": "ceo",
        "team": None,
    },
]


# =============================================================================
# CHANNEL MEMBERSHIP
# =============================================================================

CEO_AGENT_ID = AGENT_UUIDS["ceo"]

CHANNEL_MEMBERSHIPS = {
    # Cell channels - cell members + CEO
    "backend-cell": ["be-dev-1", "be-dev-2", "be-qa", "be-pm", "be-doc", "ceo"],
    "frontend-cell": ["fe-dev-1", "fe-dev-2", "fe-qa", "fe-pm", "fe-doc", "ceo"],
    "uxui-cell": ["ux-dev", "ux-qa", "ux-pm", "ux-doc", "ceo"],
    # Role channels + CEO
    "dev-all": ["be-dev-1", "be-dev-2", "fe-dev-1", "fe-dev-2", "ux-dev", "ceo"],
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
    # Broadcast channels - everyone (CEO included via DEFAULT_AGENTS)
    "announcements": [a["slug"] for a in DEFAULT_AGENTS],
    "all-hands": [a["slug"] for a in DEFAULT_AGENTS],
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
- ux-dev: UX/UI Developer
- ux-qa: UX/UI QA
- ux-pm: UX/UI PM (me)
- ux-doc: UX/UI Documenter

**Our Focus:**
- Design systems
- User research
- Prototyping
- Accessibility

Check `roboco_task_scan(team="uxui")` for pending UX/UI tasks.
""",
    },
}
