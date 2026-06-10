"""
Token Usage Analytics API

Provides endpoints for querying token usage metrics across agents,
teams, and models. Supports period-based queries (24h, 7d, 30d).
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query

from roboco.api.deps import DbSession
from roboco.services.usage import get_usage_service

router = APIRouter()

_PeriodType = Literal["24h", "7d", "30d"]

_PeriodQuery = Annotated[
    _PeriodType,
    Query(description="Time period: 24h, 7d, 30d"),
]


# =============================================================================
# SUMMARY
# =============================================================================


@router.get("/summary")
async def get_usage_summary(
    db: DbSession,
    period: _PeriodQuery = "24h",
) -> dict[str, Any]:
    """Return aggregated token usage and cost for the given period.

    Response includes:
    - tokens_input: total prompt tokens consumed
    - tokens_output: total completion tokens generated
    - total_tokens: sum of all token types
    - total_cost_usd: estimated USD cost
    - trend_pct: percent change vs. previous equivalent period
    """
    svc = get_usage_service(db)
    return await svc.get_summary(period)


# =============================================================================
# TIME SERIES
# =============================================================================


@router.get("/time-series")
async def get_usage_time_series(
    db: DbSession,
    period: _PeriodQuery = "24h",
) -> list[dict[str, Any]]:
    """Return bucketed time-series data points.

    - 24h → hourly buckets
    - 7d / 30d → daily buckets

    Each point has: bucket (ISO timestamp), tokens_input, tokens_output,
    total_tokens, cost_usd.
    """
    svc = get_usage_service(db)
    return await svc.get_time_series(period)


# =============================================================================
# BREAKDOWN ENDPOINTS
# =============================================================================


@router.get("/by-agent")
async def get_usage_by_agent(
    db: DbSession,
    period: _PeriodQuery = "24h",
) -> list[dict[str, Any]]:
    """Return per-agent token usage with pct_of_total.

    pct_of_total fields sum to approximately 100%.
    """
    svc = get_usage_service(db)
    return await svc.get_by_agent(period)


@router.get("/by-team")
async def get_usage_by_team(
    db: DbSession,
    period: _PeriodQuery = "24h",
) -> list[dict[str, Any]]:
    """Return per-team token usage with pct_of_total.

    pct_of_total fields sum to approximately 100%.
    """
    svc = get_usage_service(db)
    return await svc.get_by_team(period)


@router.get("/by-model")
async def get_usage_by_model(
    db: DbSession,
    period: _PeriodQuery = "24h",
) -> list[dict[str, Any]]:
    """Return per-model token usage with pct_of_total.

    pct_of_total fields sum to approximately 100%.
    """
    svc = get_usage_service(db)
    return await svc.get_by_model(period)


# =============================================================================
# PROJECTION
# =============================================================================


@router.get("/projection")
async def get_usage_projection(
    db: DbSession,
) -> dict[str, Any]:
    """Return projected monthly cost based on 7-day rolling average.

    projected_monthly_cost_usd is computed from avg_daily_cost * 30.
    """
    svc = get_usage_service(db)
    return await svc.get_projection()


# =============================================================================
# CACHE EFFICIENCY
# =============================================================================


@router.get("/cache-efficiency")
async def get_cache_efficiency(
    db: DbSession,
    period: _PeriodQuery = "24h",
) -> dict[str, Any]:
    """Return cache hit rate and estimated savings from prompt caching.

    - cache_hit_rate: fraction of input-like tokens served from cache
    - cost_saved_by_cache_usd: estimated savings vs. full input pricing
    """
    svc = get_usage_service(db)
    return await svc.get_cache_efficiency(period)
