"""
Token Usage Analytics API

Provides endpoints for querying token usage metrics across agents,
teams, and models. Supports period-based queries (24h, 7d, 30d).
"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query

from roboco.api.deps import DbSession, require_panel_token
from roboco.services.usage import get_usage_service

router = APIRouter(dependencies=[Depends(require_panel_token)])

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
    agent_slug: Annotated[
        str | None,
        Query(description="Restrict to one agent's spawn sessions"),
    ] = None,
) -> list[dict[str, Any]]:
    """Return bucketed time-series data points.

    - 24h → hourly buckets
    - 7d / 30d → daily buckets

    Each point has: bucket (ISO timestamp), tokens_input, tokens_output,
    total_tokens, cost_usd. When ``agent_slug`` is set the series is scoped to
    that agent's spawn sessions (the per-agent sparkline on the agent detail
    page); otherwise the whole fleet is summed.
    """
    svc = get_usage_service(db)
    return await svc.get_time_series(period, agent_slug=agent_slug)


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


@router.get("/by-role")
async def get_usage_by_role(
    db: DbSession,
    period: _PeriodQuery = "24h",
) -> list[dict[str, Any]]:
    """Return per-role token usage with cache hit rate + pct_of_total.

    Each row carries: role, tokens_input/output, tokens_cache_read/write,
    cache_hit_rate, total_tokens, cost_usd, pct_of_total.
    """
    svc = get_usage_service(db)
    return await svc.get_by_role(period)


# =============================================================================
# SPAWN WASTE
# =============================================================================


@router.get("/spawn-waste")
async def get_usage_spawn_waste(
    db: DbSession,
    period: _PeriodQuery = "24h",
) -> dict[str, Any]:
    """Return spawn-churn signals for the period.

    - by_role: per-role spawn count + unproductive count (0 output tokens) + pct
    - respawn_strikes: current wedged agent/task pairs the circuit breaker counts
    - total_spawns / unproductive_spawns / unproductive_pct: fleet totals
    """
    svc = get_usage_service(db)
    return await svc.get_spawn_waste(period)


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


# =============================================================================
# SESSIONS
# =============================================================================


@router.get("/sessions")
async def get_usage_sessions(
    db: DbSession,
    limit: Annotated[
        int, Query(ge=1, le=200, description="Max sessions to return")
    ] = 50,
) -> list[dict[str, Any]]:
    """Return the most recent agent spawn sessions, newest first.

    Each row carries per-session token totals (input / output / cache) and the
    estimated cost — the raw rows behind the aggregate usage panels.
    """
    svc = get_usage_service(db)
    return await svc.get_recent_sessions(limit)
