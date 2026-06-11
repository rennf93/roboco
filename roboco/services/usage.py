"""
Usage Analytics Service

Provides token usage analytics over agent_spawn_sessions and
daily_usage_rollups tables. Supports period-based queries (24h, 7d, 30d)
and aggregation by agent, team, and model.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from sqlalchemy.orm import InstrumentedAttribute

from roboco.db.tables import AgentSpawnSessionTable, DailyUsageRollupTable
from roboco.services.base import BaseService


def _row_tokens(row: Any) -> tuple[int, int, int, int]:
    """Extract (input, output, cache_read, cache_write) token counts as ints.

    Centralizes the null-coalescing that would otherwise be repeated across
    every aggregation method.
    """
    return (
        int(row.tokens_input or 0),
        int(row.tokens_output or 0),
        int(row.tokens_cache_read or 0),
        int(row.tokens_cache_write or 0),
    )


def _session_row(row: Any) -> dict[str, Any]:
    """Shape one spawn-session row for the dashboard's sessions table."""
    tin, tout, tcr, tcw = _row_tokens(row)
    return {
        "id": str(row.id),
        "agent_slug": row.agent_slug,
        "model": row.model,
        "started_at": row.started_at.isoformat(),
        "ended_at": row.ended_at.isoformat() if row.ended_at else None,
        "tokens_input": tin,
        "tokens_output": tout,
        "tokens_cache": tcr + tcw,
        "total_tokens": tin + tout + tcr + tcw,
        "cost": float(row.estimated_cost_usd or 0.0),
    }


def _parse_period(period: str) -> tuple[datetime, int]:
    """Parse period string into (start_dt, hours).

    Accepts '24h', '7d', '30d'. Defaults to 24h for unknown values.
    Returns (start_datetime_utc, total_hours).
    """
    now = datetime.now(UTC)
    if period == "7d":
        return now - timedelta(days=7), 7 * 24
    if period == "30d":
        return now - timedelta(days=30), 30 * 24
    # default 24h
    return now - timedelta(hours=24), 24


class UsageService(BaseService):
    """Analytics service for token usage data."""

    # =========================================================================
    # SUMMARY
    # =========================================================================

    async def get_summary(self, period: str = "24h") -> dict[str, Any]:
        """Return aggregated token and cost totals for the given period.

        Queries daily_usage_rollups for whole-day periods; falls back to
        agent_spawn_sessions for sub-day precision.

        Returns dict with: tokens_input, tokens_output, total_tokens,
        total_cost_usd, trend_pct.
        """
        start_dt, hours = _parse_period(period)

        # Current period totals from closed sessions
        result = await self.session.execute(
            select(
                func.coalesce(func.sum(AgentSpawnSessionTable.tokens_input), 0).label(
                    "tokens_input"
                ),
                func.coalesce(func.sum(AgentSpawnSessionTable.tokens_output), 0).label(
                    "tokens_output"
                ),
                func.coalesce(
                    func.sum(AgentSpawnSessionTable.tokens_cache_read), 0
                ).label("tokens_cache_read"),
                func.coalesce(
                    func.sum(AgentSpawnSessionTable.tokens_cache_write), 0
                ).label("tokens_cache_write"),
                func.coalesce(
                    func.sum(AgentSpawnSessionTable.estimated_cost_usd), 0.0
                ).label("total_cost_usd"),
            ).where(
                AgentSpawnSessionTable.started_at >= start_dt,
                AgentSpawnSessionTable.ended_at.isnot(None),
            )
        )
        row = result.one()
        tokens_input = int(row.tokens_input or 0)
        tokens_output = int(row.tokens_output or 0)
        total_cost = float(row.total_cost_usd or 0.0)
        total_tokens = (
            tokens_input
            + tokens_output
            + int(row.tokens_cache_read or 0)
            + int(row.tokens_cache_write or 0)
        )

        # Previous period for trend calculation.
        # Sum all 4 token columns so the comparison is consistent with the
        # current-period total_tokens (which also sums all 4 columns).
        prev_start = start_dt - timedelta(hours=hours)
        prev_result = await self.session.execute(
            select(
                func.coalesce(
                    func.sum(
                        AgentSpawnSessionTable.tokens_input
                        + AgentSpawnSessionTable.tokens_output
                        + AgentSpawnSessionTable.tokens_cache_read
                        + AgentSpawnSessionTable.tokens_cache_write
                    ),
                    0,
                ).label("total")
            ).where(
                AgentSpawnSessionTable.started_at >= prev_start,
                AgentSpawnSessionTable.started_at < start_dt,
                AgentSpawnSessionTable.ended_at.isnot(None),
            )
        )
        prev_row = prev_result.one()
        prev_total = int(prev_row.total or 0)

        if prev_total > 0:
            trend_pct = round((total_tokens - prev_total) / prev_total * 100, 1)
        elif total_tokens > 0:
            trend_pct = 100.0
        else:
            trend_pct = 0.0

        return {
            "tokens_input": tokens_input,
            "tokens_output": tokens_output,
            "total_tokens": total_tokens,
            "total_cost_usd": round(total_cost, 6),
            "trend_pct": trend_pct,
            "period": period,
        }

    # =========================================================================
    # TIME SERIES
    # =========================================================================

    async def get_time_series(self, period: str = "24h") -> list[dict[str, Any]]:
        """Return bucketed time-series data points.

        - 24h → hourly buckets
        - 7d / 30d → daily buckets

        Each point has: bucket (ISO string), tokens_input, tokens_output,
        total_tokens, cost_usd.

        total_tokens includes all 4 token types (input + output + cache_read +
        cache_write) so it is consistent with get_summary()'s total_tokens
        field — the two sums must match for the same period.
        """
        start_dt, _hours = _parse_period(period)

        if period == "24h":
            # Hourly buckets
            trunc_fn = func.date_trunc("hour", AgentSpawnSessionTable.started_at)
        else:
            # Daily buckets
            trunc_fn = func.date_trunc("day", AgentSpawnSessionTable.started_at)

        result = await self.session.execute(
            select(
                trunc_fn.label("bucket"),
                func.coalesce(func.sum(AgentSpawnSessionTable.tokens_input), 0).label(
                    "tokens_input"
                ),
                func.coalesce(func.sum(AgentSpawnSessionTable.tokens_output), 0).label(
                    "tokens_output"
                ),
                func.coalesce(
                    func.sum(AgentSpawnSessionTable.tokens_cache_read), 0
                ).label("tokens_cache_read"),
                func.coalesce(
                    func.sum(AgentSpawnSessionTable.tokens_cache_write), 0
                ).label("tokens_cache_write"),
                func.coalesce(
                    func.sum(AgentSpawnSessionTable.estimated_cost_usd), 0.0
                ).label("cost_usd"),
            )
            .where(
                AgentSpawnSessionTable.started_at >= start_dt,
                AgentSpawnSessionTable.ended_at.isnot(None),
            )
            .group_by(trunc_fn)
            .order_by(trunc_fn)
        )
        rows = result.fetchall()

        points = []
        for r in rows:
            ti = int(r.tokens_input or 0)
            to_ = int(r.tokens_output or 0)
            tcr = int(r.tokens_cache_read or 0)
            tcw = int(r.tokens_cache_write or 0)
            points.append(
                {
                    "bucket": r.bucket.isoformat() if r.bucket else None,
                    "tokens_input": ti,
                    "tokens_output": to_,
                    "total_tokens": ti + to_ + tcr + tcw,
                    "cost_usd": round(float(r.cost_usd or 0.0), 6),
                }
            )
        return points

    # =========================================================================
    # BY-DIMENSION (agent / team / model)
    # =========================================================================

    async def _aggregate_by(
        self,
        group_column: InstrumentedAttribute[Any],
        key_name: str,
        period: str,
    ) -> list[dict[str, Any]]:
        """Aggregate token usage grouped by an arbitrary column.

        Shared implementation behind get_by_agent/get_by_team/get_by_model.
        ``key_name`` is the dict key the grouping value is emitted under
        (e.g. "agent_slug"). Rows are ordered by input+output desc and each
        item carries pct_of_total computed against the grand total.
        """
        start_dt, _ = _parse_period(period)

        result = await self.session.execute(
            select(
                group_column.label(key_name),
                func.coalesce(func.sum(AgentSpawnSessionTable.tokens_input), 0).label(
                    "tokens_input"
                ),
                func.coalesce(func.sum(AgentSpawnSessionTable.tokens_output), 0).label(
                    "tokens_output"
                ),
                func.coalesce(
                    func.sum(AgentSpawnSessionTable.tokens_cache_read), 0
                ).label("tokens_cache_read"),
                func.coalesce(
                    func.sum(AgentSpawnSessionTable.tokens_cache_write), 0
                ).label("tokens_cache_write"),
                func.coalesce(
                    func.sum(AgentSpawnSessionTable.estimated_cost_usd), 0.0
                ).label("cost_usd"),
            )
            .where(
                AgentSpawnSessionTable.started_at >= start_dt,
                AgentSpawnSessionTable.ended_at.isnot(None),
            )
            .group_by(group_column)
            .order_by(
                func.sum(
                    AgentSpawnSessionTable.tokens_input
                    + AgentSpawnSessionTable.tokens_output
                ).desc()
            )
        )
        rows = result.fetchall()

        grand_total = sum(sum(_row_tokens(r)) for r in rows)
        items = []
        for r in rows:
            ti, to_, tcr, tcw = _row_tokens(r)
            total = ti + to_ + tcr + tcw
            items.append(
                {
                    key_name: getattr(r, key_name),
                    "tokens_input": ti,
                    "tokens_output": to_,
                    "total_tokens": total,
                    "cost_usd": round(float(r.cost_usd or 0.0), 6),
                    "pct_of_total": round(total / grand_total * 100, 2)
                    if grand_total > 0
                    else 0.0,
                }
            )
        return items

    async def get_by_agent(self, period: str = "24h") -> list[dict[str, Any]]:
        """Return per-agent token usage with pct_of_total."""
        return await self._aggregate_by(
            AgentSpawnSessionTable.agent_slug, "agent_slug", period
        )

    async def get_by_team(self, period: str = "24h") -> list[dict[str, Any]]:
        """Return per-team token usage with pct_of_total."""
        return await self._aggregate_by(AgentSpawnSessionTable.team, "team", period)

    async def get_by_model(self, period: str = "24h") -> list[dict[str, Any]]:
        """Return per-model token usage with pct_of_total."""
        return await self._aggregate_by(AgentSpawnSessionTable.model, "model", period)

    # =========================================================================
    # PROJECTION
    # =========================================================================

    async def get_projection(self) -> dict[str, Any]:
        """Return projected monthly cost based on 7-day rolling average.

        Computes the average daily cost over the last 7 days and extrapolates
        to 30 days.
        """
        seven_days_ago = datetime.now(UTC) - timedelta(days=7)

        result = await self.session.execute(
            select(
                func.coalesce(
                    func.sum(AgentSpawnSessionTable.estimated_cost_usd), 0.0
                ).label("total_cost_7d"),
                func.coalesce(func.count(AgentSpawnSessionTable.id), 0).label(
                    "session_count"
                ),
            ).where(
                AgentSpawnSessionTable.started_at >= seven_days_ago,
                AgentSpawnSessionTable.ended_at.isnot(None),
            )
        )
        row = result.one()
        total_cost_7d = float(row.total_cost_7d or 0.0)
        avg_daily_cost = total_cost_7d / 7.0
        projected_monthly = avg_daily_cost * 30.0

        return {
            "total_cost_7d": round(total_cost_7d, 6),
            "avg_daily_cost_usd": round(avg_daily_cost, 6),
            "projected_monthly_cost_usd": round(projected_monthly, 4),
            "basis_days": 7,
        }

    # =========================================================================
    # CACHE EFFICIENCY
    # =========================================================================

    async def get_cache_efficiency(self, period: str = "24h") -> dict[str, Any]:
        """Return cache hit rate and estimated savings from prompt caching.

        cache_hit_rate = cache_read_tokens / (input_tokens + cache_read_tokens)
        cost_saved = what cache reads would have cost at full input price
                     minus what they actually cost at cache-read price.
        """
        start_dt, _ = _parse_period(period)

        result = await self.session.execute(
            select(
                func.coalesce(func.sum(AgentSpawnSessionTable.tokens_input), 0).label(
                    "tokens_input"
                ),
                func.coalesce(func.sum(AgentSpawnSessionTable.tokens_output), 0).label(
                    "tokens_output"
                ),
                func.coalesce(
                    func.sum(AgentSpawnSessionTable.tokens_cache_read), 0
                ).label("tokens_cache_read"),
                func.coalesce(
                    func.sum(AgentSpawnSessionTable.tokens_cache_write), 0
                ).label("tokens_cache_write"),
            ).where(
                AgentSpawnSessionTable.started_at >= start_dt,
                AgentSpawnSessionTable.ended_at.isnot(None),
            )
        )
        row = result.one()
        tokens_input = int(row.tokens_input or 0)
        tokens_cache_read = int(row.tokens_cache_read or 0)
        tokens_cache_write = int(row.tokens_cache_write or 0)

        total_input_like = tokens_input + tokens_cache_read
        cache_hit_rate = (
            tokens_cache_read / total_input_like if total_input_like > 0 else 0.0
        )

        # Cost saved = (cache_read_tokens * full_input_rate) - actual_cache_read_cost
        # Use sonnet as the baseline (most common model) for the aggregate estimate.
        # The full input price for sonnet is $3/1M; cache read is $0.30/1M.
        _MILLION = 1_000_000.0
        _FULL_INPUT_PRICE = 3.00  # sonnet baseline, USD/1M
        _CACHE_READ_PRICE = 0.30  # 10% of input
        cost_at_full_price = tokens_cache_read * _FULL_INPUT_PRICE / _MILLION
        cost_at_cache_price = tokens_cache_read * _CACHE_READ_PRICE / _MILLION
        cost_saved = cost_at_full_price - cost_at_cache_price

        return {
            "cache_hit_rate": round(cache_hit_rate, 4),
            "tokens_cache_read": tokens_cache_read,
            "tokens_cache_write": tokens_cache_write,
            "tokens_input": tokens_input,
            "cost_saved_by_cache_usd": round(cost_saved, 6),
            "period": period,
        }

    # =========================================================================
    # TODAY'S USAGE (for CEO dashboard)
    # =========================================================================

    async def get_today_summary(self) -> dict[str, Any]:
        """Return today's aggregated usage from daily_usage_rollups.

        Used by the CEO dashboard to populate tokens_today and cost_today_usd.
        Falls back to 0 values when no data exists for today.
        """
        today = datetime.now(UTC).date()

        result = await self.session.execute(
            select(
                func.coalesce(func.sum(DailyUsageRollupTable.tokens_input), 0).label(
                    "tokens_input"
                ),
                func.coalesce(func.sum(DailyUsageRollupTable.tokens_output), 0).label(
                    "tokens_output"
                ),
                func.coalesce(
                    func.sum(DailyUsageRollupTable.tokens_cache_read), 0
                ).label("tokens_cache_read"),
                func.coalesce(
                    func.sum(DailyUsageRollupTable.tokens_cache_write), 0
                ).label("tokens_cache_write"),
                func.coalesce(
                    func.sum(DailyUsageRollupTable.total_cost_usd), 0.0
                ).label("total_cost_usd"),
            ).where(DailyUsageRollupTable.date == today)
        )
        row = result.one()

        tokens_today = (
            int(row.tokens_input or 0)
            + int(row.tokens_output or 0)
            + int(row.tokens_cache_read or 0)
            + int(row.tokens_cache_write or 0)
        )

        return {
            "tokens_today": tokens_today,
            "cost_today_usd": round(float(row.total_cost_usd or 0.0), 6),
        }

    async def get_recent_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent spawn sessions, newest first.

        These are the raw per-session rows behind the aggregate panels — the
        dashboard's "Recent Sessions" table.
        """
        result = await self.session.execute(
            select(AgentSpawnSessionTable)
            .order_by(AgentSpawnSessionTable.started_at.desc())
            .limit(limit)
        )
        return [_session_row(row) for row in result.scalars().all()]


def get_usage_service(db: AsyncSession) -> UsageService:
    """Factory function matching the pattern used by other services."""
    return UsageService(db)
