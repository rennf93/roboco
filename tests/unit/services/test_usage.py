"""
Unit tests for roboco.services.usage — UsageService analytics methods.

These tests mock the SQLAlchemy AsyncSession.execute() boundary and
verify the arithmetic / logic of each analytics method:

- get_summary: trend_pct edge cases (prev=0, curr=0, both=0, prev>0)
- get_by_agent/team/model: pct_of_total sums to 100%
- get_projection: projected_monthly = avg_daily * 30
- get_cache_efficiency: cache_hit_rate and cost_saved arithmetic
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.services.usage import UsageService

# ---------------------------------------------------------------------------
# Named constants (ruff PLR2004: magic values in comparisons must be named).
# ---------------------------------------------------------------------------

# Tolerance for floating-point arithmetic comparisons.
_TOL = 0.001
# Tolerance for percentage-sum assertions (rounding in pct_of_total).
_PCT_TOL = 0.1

# token count helpers
_ZERO = 0
_M = 1_000_000

# Expected values for projection tests
_COST_7D = 70.0
_EXPECTED_AVG_DAILY = 10.0  # 70 / 7
_EXPECTED_MONTHLY = 300.0  # 10 * 30
_DAYS_BASIS = 7

# Expected values for cache efficiency tests
_CACHE_READ_TOKENS = 400
_INPUT_TOKENS = 600
_EXPECTED_HIT_RATE = 0.4  # 400 / (600 + 400)
_FULL_INPUT_PRICE = 3.00  # sonnet baseline USD/1M
_CACHE_READ_PRICE = 0.30
_EXPECTED_COST_SAVED = _FULL_INPUT_PRICE - _CACHE_READ_PRICE  # = 2.70 per 1M

# Expected trend_pct values
_TREND_NONE = 0.0
_TREND_NEW = 100.0  # curr > 0, prev == 0
_TREND_DOUBLED = 200.0  # curr / prev = 3.0x → +200 %
_TREND_HALVED = -50.0  # curr / prev = 0.5x → -50 %

# Expected total_tokens when cache tokens are included
_TOTAL_WITH_CACHE = 300  # 100+100+50+50

# pct_of_total checks
_FULL_PCT = 100.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_row(**kwargs: object) -> MagicMock:
    """Return a MagicMock that mimics a SQLAlchemy Row with named attributes."""
    row = MagicMock()
    for k, v in kwargs.items():
        setattr(row, k, v)
    return row


def _result_one(row: MagicMock) -> MagicMock:
    """Return a mock execute() result whose .one() returns `row`."""
    result = MagicMock()
    result.one = MagicMock(return_value=row)
    return result


def _result_fetchall(rows: list[MagicMock]) -> MagicMock:
    """Return a mock execute() result whose .fetchall() returns `rows`."""
    result = MagicMock()
    result.fetchall = MagicMock(return_value=rows)
    return result


def _service_with_execute(*return_values: object) -> UsageService:
    """Build a UsageService whose session.execute() returns the provided
    values in sequence (one per call)."""
    session = MagicMock()
    session.execute = AsyncMock(side_effect=list(return_values))
    return UsageService(session)


# ---------------------------------------------------------------------------
# get_summary — trend_pct arithmetic
# ---------------------------------------------------------------------------


class TestGetSummaryTrendPct:
    @pytest.mark.asyncio
    async def test_both_zero_returns_zero_trend(self) -> None:
        """When current and previous totals are both 0, trend_pct must be 0.0."""
        current_row = _make_row(
            tokens_input=_ZERO,
            tokens_output=_ZERO,
            tokens_cache_read=_ZERO,
            tokens_cache_write=_ZERO,
            total_cost_usd=0.0,
        )
        prev_row = _make_row(total=_ZERO)
        svc = _service_with_execute(_result_one(current_row), _result_one(prev_row))
        result = await svc.get_summary("24h")
        assert result["trend_pct"] == _TREND_NONE

    @pytest.mark.asyncio
    async def test_prev_zero_curr_positive_returns_100(self) -> None:
        """When prev period is 0 but current is positive, trend_pct = 100.0."""
        current_row = _make_row(
            tokens_input=500,
            tokens_output=500,
            tokens_cache_read=_ZERO,
            tokens_cache_write=_ZERO,
            total_cost_usd=0.01,
        )
        prev_row = _make_row(total=_ZERO)
        svc = _service_with_execute(_result_one(current_row), _result_one(prev_row))
        result = await svc.get_summary("24h")
        assert result["trend_pct"] == _TREND_NEW

    @pytest.mark.asyncio
    async def test_positive_trend_calculation(self) -> None:
        """trend_pct = (current - previous) / previous * 100 when prev > 0.

        current = 1500 input + 1500 output = 3000; prev = 1000
        → (3000 - 1000) / 1000 * 100 = 200.0
        """
        current_row = _make_row(
            tokens_input=1500,
            tokens_output=1500,
            tokens_cache_read=_ZERO,
            tokens_cache_write=_ZERO,
            total_cost_usd=0.1,
        )
        prev_row = _make_row(total=1000)
        svc = _service_with_execute(_result_one(current_row), _result_one(prev_row))
        result = await svc.get_summary("24h")
        assert abs(result["trend_pct"] - _TREND_DOUBLED) < _TOL

    @pytest.mark.asyncio
    async def test_negative_trend_calculation(self) -> None:
        """Negative trend when usage drops.

        current = 250 + 250 = 500; prev = 1000
        → (500 - 1000) / 1000 * 100 = -50.0
        """
        current_row = _make_row(
            tokens_input=250,
            tokens_output=250,
            tokens_cache_read=_ZERO,
            tokens_cache_write=_ZERO,
            total_cost_usd=0.01,
        )
        prev_row = _make_row(total=1000)
        svc = _service_with_execute(_result_one(current_row), _result_one(prev_row))
        result = await svc.get_summary("24h")
        assert abs(result["trend_pct"] - _TREND_HALVED) < _TOL

    @pytest.mark.asyncio
    async def test_cache_tokens_included_in_total(self) -> None:
        """total_tokens includes cache_read and cache_write tokens."""
        current_row = _make_row(
            tokens_input=100,
            tokens_output=100,
            tokens_cache_read=50,
            tokens_cache_write=50,
            total_cost_usd=0.005,
        )
        prev_row = _make_row(total=_ZERO)
        svc = _service_with_execute(_result_one(current_row), _result_one(prev_row))
        result = await svc.get_summary("24h")
        assert result["total_tokens"] == _TOTAL_WITH_CACHE

    @pytest.mark.asyncio
    async def test_summary_contains_required_fields(self) -> None:
        """Response dict must include all required summary fields."""
        current_row = _make_row(
            tokens_input=_ZERO,
            tokens_output=_ZERO,
            tokens_cache_read=_ZERO,
            tokens_cache_write=_ZERO,
            total_cost_usd=0.0,
        )
        prev_row = _make_row(total=_ZERO)
        svc = _service_with_execute(_result_one(current_row), _result_one(prev_row))
        result = await svc.get_summary("24h")
        for field in ("tokens_input", "tokens_output", "total_cost_usd", "trend_pct"):
            assert field in result, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# get_by_agent — pct_of_total sums to 100%
# ---------------------------------------------------------------------------


class TestGetByAgent:
    @pytest.mark.asyncio
    async def test_pct_of_total_sums_to_100(self) -> None:
        rows = [
            _make_row(
                agent_slug="be-dev-1",
                tokens_input=600,
                tokens_output=400,
                cost_usd=0.05,
            ),
            _make_row(
                agent_slug="be-dev-2",
                tokens_input=300,
                tokens_output=200,
                cost_usd=0.02,
            ),
            _make_row(
                agent_slug="be-qa",
                tokens_input=100,
                tokens_output=100,
                cost_usd=0.01,
            ),
        ]
        svc = _service_with_execute(_result_fetchall(rows))
        result = await svc.get_by_agent("24h")
        total_pct = sum(item["pct_of_total"] for item in result)
        assert abs(total_pct - _FULL_PCT) < _PCT_TOL

    @pytest.mark.asyncio
    async def test_empty_result_returns_empty_list(self) -> None:
        svc = _service_with_execute(_result_fetchall([]))
        result = await svc.get_by_agent("24h")
        assert result == []

    @pytest.mark.asyncio
    async def test_single_agent_has_100_pct(self) -> None:
        rows = [
            _make_row(
                agent_slug="be-dev-1",
                tokens_input=1000,
                tokens_output=500,
                cost_usd=0.1,
            )
        ]
        svc = _service_with_execute(_result_fetchall(rows))
        result = await svc.get_by_agent("24h")
        assert len(result) == 1
        assert result[_ZERO]["pct_of_total"] == _FULL_PCT

    @pytest.mark.asyncio
    async def test_result_contains_agent_slug_field(self) -> None:
        rows = [
            _make_row(
                agent_slug="be-dev-1",
                tokens_input=100,
                tokens_output=100,
                cost_usd=0.01,
            )
        ]
        svc = _service_with_execute(_result_fetchall(rows))
        result = await svc.get_by_agent()
        assert result[_ZERO]["agent_slug"] == "be-dev-1"


# ---------------------------------------------------------------------------
# get_by_team — pct_of_total sums to 100%
# ---------------------------------------------------------------------------


class TestGetByTeam:
    @pytest.mark.asyncio
    async def test_pct_of_total_sums_to_100(self) -> None:
        rows = [
            _make_row(
                team="backend", tokens_input=700, tokens_output=300, cost_usd=0.05
            ),
            _make_row(
                team="frontend",
                tokens_input=200,
                tokens_output=200,
                cost_usd=0.02,
            ),
            _make_row(team="uxui", tokens_input=100, tokens_output=100, cost_usd=0.01),
        ]
        svc = _service_with_execute(_result_fetchall(rows))
        result = await svc.get_by_team("24h")
        total_pct = sum(item["pct_of_total"] for item in result)
        assert abs(total_pct - _FULL_PCT) < _PCT_TOL

    @pytest.mark.asyncio
    async def test_result_contains_team_field(self) -> None:
        rows = [
            _make_row(
                team="backend", tokens_input=100, tokens_output=100, cost_usd=0.01
            )
        ]
        svc = _service_with_execute(_result_fetchall(rows))
        result = await svc.get_by_team()
        assert result[_ZERO]["team"] == "backend"


# ---------------------------------------------------------------------------
# get_by_model — pct_of_total sums to 100%
# ---------------------------------------------------------------------------


class TestGetByModel:
    @pytest.mark.asyncio
    async def test_pct_of_total_sums_to_100(self) -> None:
        rows = [
            _make_row(
                model="claude-sonnet-4-6",
                tokens_input=600,
                tokens_output=600,
                cost_usd=0.1,
            ),
            _make_row(
                model="claude-haiku-4-5",
                tokens_input=300,
                tokens_output=300,
                cost_usd=0.02,
            ),
            _make_row(
                model="claude-opus-4-5",
                tokens_input=100,
                tokens_output=100,
                cost_usd=0.04,
            ),
        ]
        svc = _service_with_execute(_result_fetchall(rows))
        result = await svc.get_by_model("24h")
        total_pct = sum(item["pct_of_total"] for item in result)
        assert abs(total_pct - _FULL_PCT) < _PCT_TOL

    @pytest.mark.asyncio
    async def test_result_contains_model_field(self) -> None:
        rows = [
            _make_row(
                model="claude-sonnet-4-6",
                tokens_input=100,
                tokens_output=100,
                cost_usd=0.01,
            )
        ]
        svc = _service_with_execute(_result_fetchall(rows))
        result = await svc.get_by_model()
        assert result[_ZERO]["model"] == "claude-sonnet-4-6"


# ---------------------------------------------------------------------------
# get_projection — formula: projected_monthly = (total_7d / 7) * 30
# ---------------------------------------------------------------------------


class TestGetProjection:
    @pytest.mark.asyncio
    async def test_projection_formula_30_day_extrapolation(self) -> None:
        """projected_monthly_cost_usd = (total_cost_7d / 7) * 30."""
        row = _make_row(total_cost_7d=_COST_7D, session_count=10)
        svc = _service_with_execute(_result_one(row))
        result = await svc.get_projection()
        assert abs(result["projected_monthly_cost_usd"] - _EXPECTED_MONTHLY) < _TOL

    @pytest.mark.asyncio
    async def test_zero_cost_7d_gives_zero_projection(self) -> None:
        row = _make_row(total_cost_7d=0.0, session_count=_ZERO)
        svc = _service_with_execute(_result_one(row))
        result = await svc.get_projection()
        assert result["projected_monthly_cost_usd"] == 0.0

    @pytest.mark.asyncio
    async def test_avg_daily_cost_equals_total_over_7(self) -> None:
        """avg_daily = total_7d / 7."""
        row = _make_row(total_cost_7d=21.0, session_count=5)
        svc = _service_with_execute(_result_one(row))
        result = await svc.get_projection()
        # 21 / 7 = 3.0 avg daily cost
        _avg_daily_21 = 3.0
        assert abs(result["avg_daily_cost_usd"] - _avg_daily_21) < _TOL

    @pytest.mark.asyncio
    async def test_projection_contains_required_fields(self) -> None:
        row = _make_row(total_cost_7d=7.0, session_count=3)
        svc = _service_with_execute(_result_one(row))
        result = await svc.get_projection()
        for field in (
            "total_cost_7d",
            "avg_daily_cost_usd",
            "projected_monthly_cost_usd",
            "basis_days",
        ):
            assert field in result, f"Missing field: {field}"
        assert result["basis_days"] == _DAYS_BASIS


# ---------------------------------------------------------------------------
# get_cache_efficiency — hit rate and cost_saved arithmetic
# ---------------------------------------------------------------------------


class TestGetCacheEfficiency:
    @pytest.mark.asyncio
    async def test_cache_hit_rate_formula(self) -> None:
        """cache_hit_rate = cache_read / (input + cache_read).

        400 cache reads out of 400+600 total = 0.4
        """
        row = _make_row(
            tokens_input=_INPUT_TOKENS,
            tokens_output=_ZERO,
            tokens_cache_read=_CACHE_READ_TOKENS,
            tokens_cache_write=_ZERO,
        )
        svc = _service_with_execute(_result_one(row))
        result = await svc.get_cache_efficiency("24h")
        assert abs(result["cache_hit_rate"] - _EXPECTED_HIT_RATE) < _TOL

    @pytest.mark.asyncio
    async def test_zero_input_tokens_gives_zero_hit_rate(self) -> None:
        """When no input or cache_read tokens, hit rate is 0.0."""
        row = _make_row(
            tokens_input=_ZERO,
            tokens_output=_ZERO,
            tokens_cache_read=_ZERO,
            tokens_cache_write=_ZERO,
        )
        svc = _service_with_execute(_result_one(row))
        result = await svc.get_cache_efficiency("24h")
        assert result["cache_hit_rate"] == 0.0

    @pytest.mark.asyncio
    async def test_full_cache_hit_gives_rate_of_1(self) -> None:
        """When all input-like tokens are cache reads, hit rate = 1.0."""
        _full_rate = 1.0
        row = _make_row(
            tokens_input=_ZERO,
            tokens_output=_ZERO,
            tokens_cache_read=1000,
            tokens_cache_write=_ZERO,
        )
        svc = _service_with_execute(_result_one(row))
        result = await svc.get_cache_efficiency("24h")
        assert abs(result["cache_hit_rate"] - _full_rate) < _TOL

    @pytest.mark.asyncio
    async def test_cost_saved_arithmetic(self) -> None:
        """cost_saved = cache_read * (full_input_price - cache_read_price) / 1M.

        Sonnet baseline: full=$3.00/1M, cache_read=$0.30/1M.
        For 1M cache-read tokens: saved = 3.00 - 0.30 = 2.70.
        """
        row = _make_row(
            tokens_input=_ZERO,
            tokens_output=_ZERO,
            tokens_cache_read=_M,
            tokens_cache_write=_ZERO,
        )
        svc = _service_with_execute(_result_one(row))
        result = await svc.get_cache_efficiency("24h")
        assert abs(result["cost_saved_by_cache_usd"] - _EXPECTED_COST_SAVED) < _TOL

    @pytest.mark.asyncio
    async def test_zero_cache_reads_gives_zero_savings(self) -> None:
        row = _make_row(
            tokens_input=1000,
            tokens_output=500,
            tokens_cache_read=_ZERO,
            tokens_cache_write=_ZERO,
        )
        svc = _service_with_execute(_result_one(row))
        result = await svc.get_cache_efficiency("24h")
        assert result["cost_saved_by_cache_usd"] == 0.0

    @pytest.mark.asyncio
    async def test_cache_efficiency_contains_required_fields(self) -> None:
        row = _make_row(
            tokens_input=_ZERO,
            tokens_output=_ZERO,
            tokens_cache_read=_ZERO,
            tokens_cache_write=_ZERO,
        )
        svc = _service_with_execute(_result_one(row))
        result = await svc.get_cache_efficiency("24h")
        for field in ("cache_hit_rate", "cost_saved_by_cache_usd"):
            assert field in result, f"Missing field: {field}"
