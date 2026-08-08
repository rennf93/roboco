"""
Research Route Helpers

Route-glue helpers backing roboco/api/routes/research.py.
"""

from fastapi import HTTPException, status

from roboco.api.deps import CurrentAgentContext
from roboco.config import settings
from roboco.models import AgentRole
from roboco.services.research_quota import ResearchQuotaTracker

# Board (Product Owner + Head of Marketing) and PMs research the market; the
# CEO is included so the operator can drive the same surface from the panel.
RESEARCH_ROLES = frozenset(
    {
        AgentRole.PRODUCT_OWNER,
        AgentRole.HEAD_MARKETING,
        AgentRole.MAIN_PM,
        AgentRole.CELL_PM,
        AgentRole.CEO,
    }
)

# Module-level so the Redis client is pooled across requests.
_quota_tracker = ResearchQuotaTracker()


def require_research_role(agent: CurrentAgentContext) -> None:
    if agent.role not in RESEARCH_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"role '{agent.role}' may not use web research",
        )


async def enforce_quota(agent: CurrentAgentContext) -> None:
    result = await _quota_tracker.check_and_consume(
        str(agent.agent_id), settings.research_daily_quota_per_agent
    )
    if not result.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"daily research quota exhausted "
                f"({result.limit}/day, resets {result.day} 24:00 UTC)"
            ),
        )
