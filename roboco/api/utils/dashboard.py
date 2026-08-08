"""
Dashboard Route Helpers

Route-glue helpers backing roboco/api/routes/dashboard.py.
"""

from fastapi import HTTPException, status

from roboco.api.deps import CurrentAgentContext
from roboco.models import AgentRole

# The auditor flag/report mutating routes are gated to the Auditor and the
# CEO. The Auditor is the silent-observer role whose flags/reports feed the
# CEO; the CEO overrides. Mirrors ``_require_curator`` in playbooks.py and
# ``_require_ceo`` in release.py. Read-only auditor views (``GET
# /auditor/flags``, ``GET /auditor/reports``, ``GET /auditor``) stay open —
# the dashboard is observable by any authenticated operator.
_AUDITOR_OR_CEO_ROLES = frozenset({AgentRole.AUDITOR, AgentRole.CEO})


def require_auditor_or_ceo(agent: CurrentAgentContext) -> None:
    if agent.role not in _AUDITOR_OR_CEO_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the Auditor or CEO may mutate auditor flags or reports",
        )
