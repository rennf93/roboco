"""Web-research API — pluggable external search/fetch for Board + PM roles.

Request path: agent -> roboco-search MCP -> here -> ResearchService -> provider.
The provider key lives only in this server-side process; it is never injected
into an agent container, and agents never egress (the provider's API does).
A per-agent UTC-daily quota is enforced in Redis (fails open).
"""

from fastapi import APIRouter, HTTPException, status

from roboco.api.deps import CurrentAgentContext
from roboco.api.schemas.research import (
    FetchRequest,
    FetchResponse,
    SearchRequest,
    SearchResponse,
    SearchResultItem,
)
from roboco.config import settings
from roboco.models import AgentRole
from roboco.services.research import (
    ResearchError,
    ResearchUnsupportedError,
    get_research_service,
)
from roboco.services.research_quota import ResearchQuotaTracker

router = APIRouter()

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


def _require_research_role(agent: CurrentAgentContext) -> None:
    if agent.role not in RESEARCH_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"role '{agent.role}' may not use web research",
        )


async def _enforce_quota(agent: CurrentAgentContext) -> None:
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


@router.post("/search", response_model=SearchResponse)
async def research_search(
    data: SearchRequest, agent: CurrentAgentContext
) -> SearchResponse:
    """Search the public web via the configured provider (Board + PM only)."""
    _require_research_role(agent)
    await _enforce_quota(agent)
    service = get_research_service()
    try:
        outcome = await service.search(data.query, data.max_results)
    except ResearchUnsupportedError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)
        ) from exc
    except ResearchError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"research provider error: {exc}",
        ) from exc
    finally:
        await service.close()
    return SearchResponse(
        query=outcome.query,
        provider=outcome.provider,
        answer=outcome.answer,
        results=[
            SearchResultItem(
                title=hit.title, url=hit.url, snippet=hit.snippet, score=hit.score
            )
            for hit in outcome.hits
        ],
    )


@router.post("/fetch", response_model=FetchResponse)
async def research_fetch(
    data: FetchRequest, agent: CurrentAgentContext
) -> FetchResponse:
    """Extract readable content for a URL via the provider (Board + PM only)."""
    _require_research_role(agent)
    await _enforce_quota(agent)
    service = get_research_service()
    try:
        outcome = await service.fetch(data.url, data.max_chars)
    except ResearchUnsupportedError as exc:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED, detail=str(exc)
        ) from exc
    except ResearchError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"research provider error: {exc}",
        ) from exc
    finally:
        await service.close()
    return FetchResponse(
        url=outcome.url,
        provider=outcome.provider,
        content=outcome.content,
        truncated=outcome.truncated,
    )
