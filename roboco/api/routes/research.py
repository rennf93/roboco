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
from roboco.api.utils.research import enforce_quota as _enforce_quota
from roboco.api.utils.research import require_research_role as _require_research_role
from roboco.security import (
    guard_deco,
    internal_ssrf_validator,
    prompt_injection_validator,
)
from roboco.services.research import (
    ResearchError,
    ResearchUnsupportedError,
    get_research_service,
)

router = APIRouter()


@router.post("/search", response_model=SearchResponse)
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.max_request_size(size_bytes=65536)
@guard_deco.custom_validation(prompt_injection_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.suspicious_detection(enabled=True)
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
@guard_deco.rate_limit(requests=20, window=60)
@guard_deco.custom_validation(internal_ssrf_validator)
@guard_deco.content_type_filter(["application/json"])
@guard_deco.suspicious_detection(enabled=True)
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
