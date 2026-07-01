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

import datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

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


def _result_scalars(objs: list[MagicMock]) -> MagicMock:
    """Return a mock execute() result whose .scalars().all() returns `objs`."""
    result = MagicMock()
    result.scalars.return_value.all = MagicMock(return_value=objs)
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
# get_time_series — total_tokens includes all 4 token types (summary consistency)
# ---------------------------------------------------------------------------

# Named constants for time-series tests
_TS_INPUT = 100
_TS_OUTPUT = 200
_TS_CACHE_READ = 50
_TS_CACHE_WRITE = 30
# total = 100 + 200 + 50 + 30 = 380
_TS_TOTAL_WITH_CACHE = 380
# Without cache tokens (the old wrong formula): 100 + 200 = 300
_TS_TOTAL_WITHOUT_CACHE = 300


class TestGetTimeSeries:
    @pytest.mark.asyncio
    async def test_total_tokens_includes_cache_read_and_write(self) -> None:
        """total_tokens in each time-series point must include cache tokens.

        This is the time-series / summary consistency requirement: time-series
        total_tokens must sum to the same value as get_summary()'s total_tokens
        for the same period.  The old implementation used ti + to_ (without
        cache), which violated this constraint whenever cache tokens were non-zero.
        """

        bucket_dt = datetime.datetime(2026, 6, 9, 12, 0, 0, tzinfo=datetime.UTC)
        row = _make_row(
            bucket=bucket_dt,
            tokens_input=_TS_INPUT,
            tokens_output=_TS_OUTPUT,
            tokens_cache_read=_TS_CACHE_READ,
            tokens_cache_write=_TS_CACHE_WRITE,
            cost_usd=0.01,
        )
        svc = _service_with_execute(_result_fetchall([row]))
        result = await svc.get_time_series("24h")
        assert len(result) == 1
        assert result[0]["total_tokens"] == _TS_TOTAL_WITH_CACHE

    @pytest.mark.asyncio
    async def test_total_tokens_without_cache_still_correct(self) -> None:
        """When cache tokens are zero, total_tokens == tokens_input + tokens_output."""

        bucket_dt = datetime.datetime(2026, 6, 9, 12, 0, 0, tzinfo=datetime.UTC)
        row = _make_row(
            bucket=bucket_dt,
            tokens_input=_TS_INPUT,
            tokens_output=_TS_OUTPUT,
            tokens_cache_read=_ZERO,
            tokens_cache_write=_ZERO,
            cost_usd=0.01,
        )
        svc = _service_with_execute(_result_fetchall([row]))
        result = await svc.get_time_series("24h")
        assert result[0]["total_tokens"] == _TS_INPUT + _TS_OUTPUT

    @pytest.mark.asyncio
    async def test_empty_result_returns_empty_list(self) -> None:
        svc = _service_with_execute(_result_fetchall([]))
        result = await svc.get_time_series("24h")
        assert result == []

    @pytest.mark.asyncio
    async def test_point_contains_required_fields(self) -> None:
        """Each time-series point must have bucket, tokens_input, tokens_output,
        total_tokens, and cost_usd fields."""

        bucket_dt = datetime.datetime(2026, 6, 9, 12, 0, 0, tzinfo=datetime.UTC)
        row = _make_row(
            bucket=bucket_dt,
            tokens_input=100,
            tokens_output=100,
            tokens_cache_read=_ZERO,
            tokens_cache_write=_ZERO,
            cost_usd=0.01,
        )
        svc = _service_with_execute(_result_fetchall([row]))
        result = await svc.get_time_series("24h")
        assert len(result) == 1
        point = result[0]
        for field in (
            "bucket",
            "tokens_input",
            "tokens_output",
            "total_tokens",
            "cost_usd",
        ):
            assert field in point, f"Missing field: {field}"


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
                tokens_cache_read=0,
                tokens_cache_write=0,
                cost_usd=0.05,
            ),
            _make_row(
                agent_slug="be-dev-2",
                tokens_input=300,
                tokens_output=200,
                tokens_cache_read=0,
                tokens_cache_write=0,
                cost_usd=0.02,
            ),
            _make_row(
                agent_slug="be-qa",
                tokens_input=100,
                tokens_output=100,
                tokens_cache_read=0,
                tokens_cache_write=0,
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
                tokens_cache_read=0,
                tokens_cache_write=0,
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
                tokens_cache_read=0,
                tokens_cache_write=0,
                cost_usd=0.01,
            )
        ]
        svc = _service_with_execute(_result_fetchall(rows))
        result = await svc.get_by_agent()
        assert result[_ZERO]["agent_slug"] == "be-dev-1"

    @pytest.mark.asyncio
    async def test_cache_tokens_included_in_total_tokens(self) -> None:
        """total_tokens must include cache_read and cache_write.

        Without the fix, total would be 500+300=800 (input+output only).
        With the fix, total = 500+300+100+100 = 1000.
        """
        _cache_read = 100
        _cache_write = 100
        _expected_total = 500 + 300 + _cache_read + _cache_write  # 1000
        rows = [
            _make_row(
                agent_slug="be-dev-1",
                tokens_input=500,
                tokens_output=300,
                tokens_cache_read=_cache_read,
                tokens_cache_write=_cache_write,
                cost_usd=0.05,
            )
        ]
        svc = _service_with_execute(_result_fetchall(rows))
        result = await svc.get_by_agent("24h")
        assert result[_ZERO]["total_tokens"] == _expected_total

    @pytest.mark.asyncio
    async def test_pct_of_total_sums_to_100_with_cache_tokens(self) -> None:
        """pct_of_total still sums to 100% when agents have cache tokens."""
        rows = [
            _make_row(
                agent_slug="be-dev-1",
                tokens_input=400,
                tokens_output=200,
                tokens_cache_read=150,
                tokens_cache_write=50,
                cost_usd=0.05,
            ),
            _make_row(
                agent_slug="be-dev-2",
                tokens_input=200,
                tokens_output=100,
                tokens_cache_read=75,
                tokens_cache_write=25,
                cost_usd=0.02,
            ),
        ]
        svc = _service_with_execute(_result_fetchall(rows))
        result = await svc.get_by_agent("24h")
        total_pct = sum(item["pct_of_total"] for item in result)
        assert abs(total_pct - _FULL_PCT) < _PCT_TOL


# ---------------------------------------------------------------------------
# get_by_team — pct_of_total sums to 100%
# ---------------------------------------------------------------------------


class TestGetByTeam:
    @pytest.mark.asyncio
    async def test_pct_of_total_sums_to_100(self) -> None:
        rows = [
            _make_row(
                team="backend",
                tokens_input=700,
                tokens_output=300,
                tokens_cache_read=0,
                tokens_cache_write=0,
                cost_usd=0.05,
            ),
            _make_row(
                team="frontend",
                tokens_input=200,
                tokens_output=200,
                tokens_cache_read=0,
                tokens_cache_write=0,
                cost_usd=0.02,
            ),
            _make_row(
                team="uxui",
                tokens_input=100,
                tokens_output=100,
                tokens_cache_read=0,
                tokens_cache_write=0,
                cost_usd=0.01,
            ),
        ]
        svc = _service_with_execute(_result_fetchall(rows))
        result = await svc.get_by_team("24h")
        total_pct = sum(item["pct_of_total"] for item in result)
        assert abs(total_pct - _FULL_PCT) < _PCT_TOL

    @pytest.mark.asyncio
    async def test_result_contains_team_field(self) -> None:
        rows = [
            _make_row(
                team="backend",
                tokens_input=100,
                tokens_output=100,
                tokens_cache_read=0,
                tokens_cache_write=0,
                cost_usd=0.01,
            )
        ]
        svc = _service_with_execute(_result_fetchall(rows))
        result = await svc.get_by_team()
        assert result[_ZERO]["team"] == "backend"

    @pytest.mark.asyncio
    async def test_cache_tokens_included_in_total_tokens(self) -> None:
        """total_tokens must include cache_read and cache_write."""
        _cache_read = 200
        _cache_write = 100
        _expected_total = 700 + 300 + _cache_read + _cache_write  # 1300
        rows = [
            _make_row(
                team="backend",
                tokens_input=700,
                tokens_output=300,
                tokens_cache_read=_cache_read,
                tokens_cache_write=_cache_write,
                cost_usd=0.05,
            )
        ]
        svc = _service_with_execute(_result_fetchall(rows))
        result = await svc.get_by_team("24h")
        assert result[_ZERO]["total_tokens"] == _expected_total

    @pytest.mark.asyncio
    async def test_pct_of_total_sums_to_100_with_cache_tokens(self) -> None:
        """pct_of_total still sums to 100% when teams have cache tokens."""
        rows = [
            _make_row(
                team="backend",
                tokens_input=600,
                tokens_output=200,
                tokens_cache_read=120,
                tokens_cache_write=80,
                cost_usd=0.05,
            ),
            _make_row(
                team="frontend",
                tokens_input=300,
                tokens_output=100,
                tokens_cache_read=60,
                tokens_cache_write=40,
                cost_usd=0.02,
            ),
        ]
        svc = _service_with_execute(_result_fetchall(rows))
        result = await svc.get_by_team("24h")
        total_pct = sum(item["pct_of_total"] for item in result)
        assert abs(total_pct - _FULL_PCT) < _PCT_TOL


# ---------------------------------------------------------------------------
# get_by_model — pct_of_total sums to 100%
# ---------------------------------------------------------------------------


class TestGetByModel:
    @pytest.mark.asyncio
    async def test_pct_of_total_sums_to_100(self) -> None:
        rows = [
            _make_row(
                model="claude-sonnet-5",
                tokens_input=600,
                tokens_output=600,
                tokens_cache_read=0,
                tokens_cache_write=0,
                cost_usd=0.1,
            ),
            _make_row(
                model="claude-haiku-4-5",
                tokens_input=300,
                tokens_output=300,
                tokens_cache_read=0,
                tokens_cache_write=0,
                cost_usd=0.02,
            ),
            _make_row(
                model="claude-opus-4-5",
                tokens_input=100,
                tokens_output=100,
                tokens_cache_read=0,
                tokens_cache_write=0,
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
                model="claude-sonnet-5",
                tokens_input=100,
                tokens_output=100,
                tokens_cache_read=0,
                tokens_cache_write=0,
                cost_usd=0.01,
            )
        ]
        svc = _service_with_execute(_result_fetchall(rows))
        result = await svc.get_by_model()
        assert result[_ZERO]["model"] == "claude-sonnet-5"

    @pytest.mark.asyncio
    async def test_result_includes_cache_fields_and_hit_rate(self) -> None:
        """Breakdown rows carry cache tokens + cache_hit_rate = read/(input+read)."""
        _cache_write = 100
        rows = [
            _make_row(
                model="claude-sonnet-5",
                tokens_input=_INPUT_TOKENS,
                tokens_output=200,
                tokens_cache_read=_CACHE_READ_TOKENS,
                tokens_cache_write=_cache_write,
                cost_usd=0.05,
            )
        ]
        svc = _service_with_execute(_result_fetchall(rows))
        result = await svc.get_by_model("24h")
        item = result[_ZERO]
        assert item["tokens_cache_read"] == _CACHE_READ_TOKENS
        assert item["tokens_cache_write"] == _cache_write
        assert abs(item["cache_hit_rate"] - _EXPECTED_HIT_RATE) < _TOL

    @pytest.mark.asyncio
    async def test_cache_tokens_included_in_total_tokens(self) -> None:
        """total_tokens must include cache_read and cache_write."""
        _cache_read = 300
        _cache_write = 100
        _expected_total = 600 + 600 + _cache_read + _cache_write  # 1600
        rows = [
            _make_row(
                model="claude-sonnet-5",
                tokens_input=600,
                tokens_output=600,
                tokens_cache_read=_cache_read,
                tokens_cache_write=_cache_write,
                cost_usd=0.1,
            )
        ]
        svc = _service_with_execute(_result_fetchall(rows))
        result = await svc.get_by_model("24h")
        assert result[_ZERO]["total_tokens"] == _expected_total

    @pytest.mark.asyncio
    async def test_pct_of_total_sums_to_100_with_cache_tokens(self) -> None:
        """pct_of_total still sums to 100% when models have cache tokens."""
        rows = [
            _make_row(
                model="claude-sonnet-5",
                tokens_input=500,
                tokens_output=500,
                tokens_cache_read=200,
                tokens_cache_write=100,
                cost_usd=0.1,
            ),
            _make_row(
                model="claude-haiku-4-5",
                tokens_input=250,
                tokens_output=250,
                tokens_cache_read=100,
                tokens_cache_write=50,
                cost_usd=0.02,
            ),
        ]
        svc = _service_with_execute(_result_fetchall(rows))
        result = await svc.get_by_model("24h")
        total_pct = sum(item["pct_of_total"] for item in result)
        assert abs(total_pct - _FULL_PCT) < _PCT_TOL


# ---------------------------------------------------------------------------
# get_by_role — groups by role, carries cache fields
# ---------------------------------------------------------------------------


class TestGetByRole:
    @pytest.mark.asyncio
    async def test_groups_by_role_with_cache_fields(self) -> None:
        """get_by_role emits the role key plus cache tokens + hit rate."""
        rows = [
            _make_row(
                role="developer",
                tokens_input=_INPUT_TOKENS,
                tokens_output=200,
                tokens_cache_read=_CACHE_READ_TOKENS,
                tokens_cache_write=100,
                cost_usd=0.05,
            )
        ]
        svc = _service_with_execute(_result_fetchall(rows))
        result = await svc.get_by_role("24h")
        item = result[_ZERO]
        assert item["role"] == "developer"
        assert item["tokens_cache_read"] == _CACHE_READ_TOKENS
        assert abs(item["cache_hit_rate"] - _EXPECTED_HIT_RATE) < _TOL


# ---------------------------------------------------------------------------
# get_spawn_waste — per-role unproductive rate + respawn strikes
# ---------------------------------------------------------------------------


class TestGetSpawnWaste:
    @pytest.mark.asyncio
    async def test_computes_unproductive_pct_and_strikes(self) -> None:
        """unproductive_pct = 0-output spawns / spawns; strikes from tracker."""
        _spawns = 10
        _unproductive = 8
        _strike_count = 4
        _expected_pct = 80.0
        role_rows = [
            _make_row(role="developer", spawns=_spawns, unproductive=_unproductive)
        ]
        strike = _make_row(
            agent_slug="be-dev-1",
            task_id=UUID("11111111-1111-1111-1111-111111111111"),
            count=_strike_count,
            last_status="in_progress",
            notified=True,
        )
        svc = _service_with_execute(
            _result_fetchall(role_rows), _result_scalars([strike])
        )
        result = await svc.get_spawn_waste("24h")
        assert result["total_spawns"] == _spawns
        assert result["unproductive_spawns"] == _unproductive
        assert abs(result["unproductive_pct"] - _expected_pct) < _TOL
        assert result["by_role"][_ZERO]["role"] == "developer"
        strike_row = result["respawn_strikes"][_ZERO]
        assert strike_row["count"] == _strike_count
        assert strike_row["notified"] is True


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


# ---------------------------------------------------------------------------
# get_recent_sessions — maps spawn-session rows to the dashboard shape
# ---------------------------------------------------------------------------


class TestGetRecentSessions:
    @pytest.mark.asyncio
    async def test_shapes_rows(self) -> None:
        """Rows are mapped to id/agent/model/tokens/cache/total/cost fields."""
        exp_in, exp_out = 6, 514
        exp_cr, exp_cw = 111_032, 14_881
        exp_cost = 0.1614
        exp_count = 1
        sid = UUID("12345678-1234-5678-1234-567812345678")

        row = MagicMock()
        row.id = sid
        row.agent_slug = "product-owner"
        row.model = "claude-opus-4-6"
        row.started_at = datetime.datetime(2026, 6, 11, 20, 41, tzinfo=datetime.UTC)
        row.ended_at = datetime.datetime(2026, 6, 11, 20, 42, tzinfo=datetime.UTC)
        row.tokens_input = exp_in
        row.tokens_output = exp_out
        row.tokens_cache_read = exp_cr
        row.tokens_cache_write = exp_cw
        row.estimated_cost_usd = exp_cost

        scalars = MagicMock()
        scalars.all = MagicMock(return_value=[row])
        result = MagicMock()
        result.scalars = MagicMock(return_value=scalars)

        svc = _service_with_execute(result)
        out = await svc.get_recent_sessions(limit=10)

        assert len(out) == exp_count
        s = out[0]
        assert s["id"] == str(sid)
        assert s["agent_slug"] == "product-owner"
        assert s["model"] == "claude-opus-4-6"
        assert s["tokens_input"] == exp_in
        assert s["tokens_output"] == exp_out
        assert s["tokens_cache"] == exp_cr + exp_cw
        assert s["total_tokens"] == exp_in + exp_out + exp_cr + exp_cw
        assert s["cost"] == pytest.approx(exp_cost)
        assert s["ended_at"] is not None

    @pytest.mark.asyncio
    async def test_open_session_has_null_ended_at(self) -> None:
        """A still-running session (ended_at None) serializes ended_at as None."""
        row = MagicMock()
        row.id = "00000000-0000-0000-0000-000000000001"
        row.agent_slug = "main-pm"
        row.model = "sonnet"
        row.started_at = datetime.datetime(2026, 6, 11, 20, 0, tzinfo=datetime.UTC)
        row.ended_at = None
        row.tokens_input = _ZERO
        row.tokens_output = _ZERO
        row.tokens_cache_read = _ZERO
        row.tokens_cache_write = _ZERO
        row.estimated_cost_usd = None

        scalars = MagicMock()
        scalars.all = MagicMock(return_value=[row])
        result = MagicMock()
        result.scalars = MagicMock(return_value=scalars)

        svc = _service_with_execute(result)
        out = await svc.get_recent_sessions()

        assert out[0]["ended_at"] is None
        assert out[0]["cost"] == _ZERO
