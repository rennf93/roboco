"""
Research Route Helpers

Route-glue helpers backing roboco/api/routes/research.py.
"""

from fastapi import HTTPException, status

from roboco.api.deps import CurrentAgentContext
from roboco.config import settings
from roboco.models import AgentRole
from roboco.services.research_errors import (
    ResearchError,
    ResearchProviderError,
    ResearchSiteError,
    ResearchUnsupportedError,
    ResearchUrlRefusedError,
)
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

# Ordered (exception type, HTTP status) pairs, first match wins: the lookup
# shape of roboco/api/utils/git.py's _ERROR_STATUS_MAP. Each status names the
# party that failed the call, and the message (passed through unprefixed)
# says who and why.
# * 501: the configured provider has no such operation (Brave has no fetch).
# * 400: RoboCo's URL guard refused the URL. The route-level
#   internal_ssrf_validator (roboco/security.py) answers 400 for the same
#   class of URL, so both layers of the same refusal agree.
# * 424 Failed Dependency: the target site refused the page fetch or never
#   answered. RoboCo and its provider worked; the request depended on a site
#   that failed it. A 4xx keeps a site's refusal out of 5xx alerting, the
#   reason roboco/api/routes/orchestrator.py answers a well-formed refusal
#   with 409 rather than a 5xx. Mirroring the site's own status would read as
#   RoboCo's: this route already answers 403 for a role without research and
#   429 for an exhausted quota.
# * 502: the research provider itself failed, as a failing GitHub App API
#   (roboco/api/routes/github_app.py) or GitHub provisioning call
#   (roboco/api/utils/pitch.py) does.
_RESEARCH_ERROR_STATUS: tuple[tuple[type[ResearchError], int], ...] = (
    (ResearchUnsupportedError, status.HTTP_501_NOT_IMPLEMENTED),
    (ResearchUrlRefusedError, status.HTTP_400_BAD_REQUEST),
    (ResearchSiteError, status.HTTP_424_FAILED_DEPENDENCY),
    (ResearchProviderError, status.HTTP_502_BAD_GATEWAY),
)


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


def research_error_to_http(exc: ResearchError) -> HTTPException:
    """Answer a research failure with the status of the party that failed it.

    An unclassified ``ResearchError`` stays a 502, the status every research
    failure had before the parties were told apart.
    """
    for exc_type, code in _RESEARCH_ERROR_STATUS:
        if isinstance(exc, exc_type):
            return HTTPException(status_code=code, detail=str(exc))
    return HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
