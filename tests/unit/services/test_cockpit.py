"""roboco.services.cockpit + route — read-only company summary (mocked deps)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from roboco.api.routes import cockpit as croute
from roboco.models import AgentRole
from roboco.models.permissions import AgentContext
from roboco.services import cockpit as cm
from roboco.services.cockpit import CockpitService
from roboco.services.strategy_engine import StrategyObservation
from roboco.services.task import TaskService

_IN_PROGRESS = 2
_CLAIMED = 1
_BLOCKED = 3
_BUDGET = 100.0
_SPEND_30D = 150.0
_COMPLETED_30D = 5
_MEDIAN_LEAD_TIME = 12.5


def _agent(role: AgentRole) -> AgentContext:
    return AgentContext(agent_id=uuid4(), role=role, team=None)


def _patch(monkeypatch: pytest.MonkeyPatch) -> None:
    goals = {
        "north_star": "Win the market",
        "objectives": [{"metric": "NPS"}],
        "operating_policy": {"monthly_budget_cap": _BUDGET},
    }
    monkeypatch.setattr(
        cm,
        "get_company_goals_service",
        lambda _s: MagicMock(get=AsyncMock(return_value=goals)),
    )
    counts = {
        "in_progress": _IN_PROGRESS,
        "claimed": _CLAIMED,
        "blocked": _BLOCKED,
        "awaiting_ceo_approval": 1,
    }
    delivery_stats = {
        "completed_30d": _COMPLETED_30D,
        "median_lead_time_hours": _MEDIAN_LEAD_TIME,
    }
    monkeypatch.setattr(
        cm,
        "get_task_service",
        lambda _s: MagicMock(
            count_by_status=AsyncMock(return_value=counts),
            get_delivery_stats_30d=AsyncMock(return_value=delivery_stats),
        ),
    )
    usage = MagicMock(
        get_summary=AsyncMock(return_value={"total_cost_usd": _SPEND_30D}),
        get_projection=AsyncMock(return_value={"projected_monthly_cost_usd": 200.0}),
    )
    monkeypatch.setattr(cm, "get_usage_service", lambda _s: usage)
    monkeypatch.setattr(
        cm,
        "get_strategy_engine",
        lambda _s: MagicMock(
            assess=AsyncMock(
                return_value=[StrategyObservation(kind="idle", summary="s", detail="d")]
            )
        ),
    )
    proposed = MagicMock()
    proposed.status = "proposed"
    done = MagicMock()
    done.status = "provisioned"
    monkeypatch.setattr(
        cm,
        "get_pitch_service",
        lambda _s: MagicMock(list_pitches=AsyncMock(return_value=[proposed, done])),
    )


@pytest.mark.asyncio
async def test_summary_aggregates(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch)
    out = await CockpitService(MagicMock()).summary()
    assert out["basis"] == "proxy"
    assert out["north_star"] == "Win the market"
    assert out["delivery"]["in_flight"] == _IN_PROGRESS + _CLAIMED
    assert out["delivery"]["blocked"] == _BLOCKED
    assert out["delivery"]["completed_30d"] == _COMPLETED_30D
    assert out["delivery"]["median_lead_time_hours"] == _MEDIAN_LEAD_TIME
    assert out["spend"]["spend_30d_usd"] == _SPEND_30D
    assert out["spend"]["over_budget"] is True
    assert out["pending_pitches"] == 1
    assert out["signals"][0]["kind"] == "idle"


@pytest.mark.asyncio
async def test_route_forbidden_for_developer() -> None:
    with pytest.raises(HTTPException) as exc:
        await croute.cockpit_summary(MagicMock(), _agent(AgentRole.DEVELOPER))
    assert exc.value.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_route_ok_for_ceo(monkeypatch: pytest.MonkeyPatch) -> None:
    summary: dict[str, Any] = {
        "basis": "proxy",
        "north_star": "Win",
        "objectives": [],
        "delivery": {
            "task_counts": {},
            "in_flight": 0,
            "blocked": 0,
            "awaiting_ceo": 0,
            "completed_30d": 0,
            "median_lead_time_hours": None,
        },
        "spend": {
            "spend_30d_usd": 0.0,
            "projected_monthly_usd": None,
            "monthly_budget_cap_usd": None,
            "over_budget": False,
        },
        "pending_pitches": 0,
        "signals": [],
    }
    svc = MagicMock(summary=AsyncMock(return_value=summary))
    monkeypatch.setattr(croute, "get_cockpit_service", lambda _db: svc)
    resp = await croute.cockpit_summary(MagicMock(), _agent(AgentRole.CEO))
    assert resp.basis == "proxy"
    assert resp.spend.over_budget is False


@pytest.mark.asyncio
async def test_signals_returns_only_strategy_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The lightweight slice returns ONLY the strategy signals — none of the
    # summary fan-out (goals / spend / counts / pitches).
    _patch(monkeypatch)
    out = await CockpitService(MagicMock()).signals()
    assert list(out.keys()) == ["signals"]
    assert out["signals"][0]["kind"] == "idle"
    assert out["signals"][0]["summary"] == "s"


@pytest.mark.asyncio
async def test_signals_route_forbidden_for_developer() -> None:
    with pytest.raises(HTTPException) as exc:
        await croute.cockpit_signals(MagicMock(), _agent(AgentRole.DEVELOPER))
    assert exc.value.status_code == HTTPStatus.FORBIDDEN


@pytest.mark.asyncio
async def test_signals_route_ok_for_ceo(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = MagicMock(
        signals=AsyncMock(
            return_value={"signals": [{"kind": "idle", "summary": "s", "detail": "d"}]}
        )
    )
    monkeypatch.setattr(croute, "get_cockpit_service", lambda _db: svc)
    resp = await croute.cockpit_signals(MagicMock(), _agent(AgentRole.CEO))
    assert resp.signals[0].kind == "idle"


@pytest.mark.asyncio
async def test_get_delivery_stats_30d_no_tasks() -> None:
    """When no completed tasks exist in the 30d window, returns zeros and None."""
    session = MagicMock()
    execute_result = MagicMock()
    execute_result.all.return_value = []
    session.execute = AsyncMock(return_value=execute_result)
    stats = await TaskService(session).get_delivery_stats_30d()
    assert stats["completed_30d"] == 0
    assert stats["median_lead_time_hours"] is None


@pytest.mark.asyncio
async def test_get_delivery_stats_30d_with_tasks() -> None:
    """With completed tasks, returns count and median lead time in hours."""
    now = datetime.now(UTC)
    # Three tasks with lead times of 2h, 4h, 6h → median = 4h
    rows = [
        MagicMock(created_at=now - timedelta(hours=2), completed_at=now),
        MagicMock(created_at=now - timedelta(hours=4), completed_at=now),
        MagicMock(created_at=now - timedelta(hours=6), completed_at=now),
    ]
    session = MagicMock()
    execute_result = MagicMock()
    execute_result.all.return_value = rows
    session.execute = AsyncMock(return_value=execute_result)
    stats = await TaskService(session).get_delivery_stats_30d()
    assert stats["completed_30d"] == len(rows)
    assert stats["median_lead_time_hours"] == pytest.approx(4.0, abs=0.01)


@pytest.mark.asyncio
async def test_get_delivery_stats_30d_single_task() -> None:
    """With a single task, median equals that task's lead time."""
    now = datetime.now(UTC)
    rows = [MagicMock(created_at=now - timedelta(hours=10), completed_at=now)]
    session = MagicMock()
    execute_result = MagicMock()
    execute_result.all.return_value = rows
    session.execute = AsyncMock(return_value=execute_result)
    stats = await TaskService(session).get_delivery_stats_30d()
    assert stats["completed_30d"] == 1
    assert stats["median_lead_time_hours"] == pytest.approx(10.0, abs=0.01)
