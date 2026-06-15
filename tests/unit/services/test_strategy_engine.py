"""roboco.services.strategy_engine — assessment + notify (dormant by default)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.services import strategy_engine as se_module
from roboco.services.strategy_engine import StrategyEngine

_GOALS_WITH_DIRECTION: dict[str, Any] = {
    "north_star": "Win the market",
    "objectives": [{"metric": "NPS", "target": 50}],
    "constraints": [],
    "operating_policy": {},
}
_GOALS_EMPTY: dict[str, Any] = {
    "north_star": "",
    "objectives": [],
    "constraints": [],
    "operating_policy": {},
}


def _engine(
    monkeypatch: pytest.MonkeyPatch,
    *,
    in_flight: list[Any],
    blocked: list[Any],
    goals: dict[str, Any],
) -> StrategyEngine:
    task_svc = MagicMock()
    task_svc.list_in_progress_or_claimed = AsyncMock(return_value=in_flight)
    task_svc.list_long_running_blocked = AsyncMock(return_value=blocked)
    monkeypatch.setattr(se_module, "get_task_service", lambda _s: task_svc)
    goals_svc = MagicMock()
    goals_svc.get = AsyncMock(return_value=goals)
    monkeypatch.setattr(se_module, "get_company_goals_service", lambda _s: goals_svc)
    return StrategyEngine(MagicMock())


@pytest.mark.asyncio
async def test_idle_with_goals_observed(monkeypatch: pytest.MonkeyPatch) -> None:
    eng = _engine(monkeypatch, in_flight=[], blocked=[], goals=_GOALS_WITH_DIRECTION)
    kinds = {o.kind for o in await eng.assess()}
    assert "idle" in kinds


@pytest.mark.asyncio
async def test_no_idle_when_work_in_flight(monkeypatch: pytest.MonkeyPatch) -> None:
    eng = _engine(
        monkeypatch, in_flight=[MagicMock()], blocked=[], goals=_GOALS_WITH_DIRECTION
    )
    assert all(o.kind != "idle" for o in await eng.assess())


@pytest.mark.asyncio
async def test_no_observations_when_idle_without_goals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    eng = _engine(monkeypatch, in_flight=[], blocked=[], goals=_GOALS_EMPTY)
    assert await eng.assess() == []


@pytest.mark.asyncio
async def test_stranded_blocked_observed(monkeypatch: pytest.MonkeyPatch) -> None:
    eng = _engine(
        monkeypatch,
        in_flight=[MagicMock()],
        blocked=[MagicMock(), MagicMock()],
        goals=_GOALS_EMPTY,
    )
    assert any(o.kind == "stranded_blocked" for o in await eng.assess())


@pytest.mark.asyncio
async def test_run_cycle_disabled_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(se_module.settings, "strategy_engine_enabled", False)
    eng = _engine(monkeypatch, in_flight=[], blocked=[], goals=_GOALS_WITH_DIRECTION)
    assert await eng.run_cycle() == []


@pytest.mark.asyncio
async def test_run_cycle_enabled_notifies_ceo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(se_module.settings, "strategy_engine_enabled", True)
    eng = _engine(monkeypatch, in_flight=[], blocked=[], goals=_GOALS_WITH_DIRECTION)
    notifier = MagicMock()
    notifier.send_ack_notification = AsyncMock()
    monkeypatch.setattr(se_module, "NotificationService", lambda: notifier)

    observations = await eng.run_cycle()

    assert observations
    notifier.send_ack_notification.assert_awaited()
    _, kwargs = notifier.send_ack_notification.call_args
    assert kwargs["to_agent"] == "ceo"


@pytest.mark.asyncio
async def test_run_cycle_enabled_no_observations_no_notify(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(se_module.settings, "strategy_engine_enabled", True)
    eng = _engine(monkeypatch, in_flight=[MagicMock()], blocked=[], goals=_GOALS_EMPTY)
    notifier = MagicMock()
    notifier.send_ack_notification = AsyncMock()
    monkeypatch.setattr(se_module, "NotificationService", lambda: notifier)

    assert await eng.run_cycle() == []
    notifier.send_ack_notification.assert_not_awaited()
