"""
Initial Data Constants

Static seed data for bootstrapping the RoboCo system.
Separates data definitions from bootstrap logic.
"""

from typing import Any

from roboco.foundation import identity as _foundation

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

# The human CEO's static agent ID — used for token issuance/verification
# (agents_config.py, api/deps.py, api/websocket.py) independent of channels.
CEO_AGENT_ID = AGENT_UUIDS["ceo"]

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
    "pr-reviewer-1": {"name": "PR Reviewer"},
    "be-pr-reviewer": {"name": "Backend PR Reviewer"},
    "fe-pr-reviewer": {"name": "Frontend PR Reviewer"},
    "ux-pr-reviewer": {"name": "UX/UI PR Reviewer"},
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
