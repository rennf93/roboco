"""roboco.services.strategy_engine — assessment + notify (dormant by default)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from roboco.db.tables import (
    AgentTable,
    BoardProgramCycleTable,
    ProjectTable,
    SystemSettingTable,
    TaskTable,
)
from roboco.foundation import identity as _foundation
from roboco.models.base import AgentRole, AgentStatus, Team
from roboco.models.base import TaskStatus as TS
from roboco.services import strategy_engine as se_module
from roboco.services.strategy_engine import StrategyEngine
from roboco.services.task import (
    ROADMAP_SOURCE,
    X_FEATURE_EXPLORATION_SOURCE,
    get_task_service,
)
from sqlalchemy import delete, update

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

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


@pytest_asyncio.fixture(autouse=True)
async def _purge_board_program_pollution(db_session: AsyncSession) -> None:
    """See ``test_board_program_engine.py``'s identical fixture: Board
    Program settings-store rows / ledger rows / open exploration tasks are
    shared, cross-test-persistent DB state that a sibling suite (this
    module's own idle-trigger tests, or the write-route
    ``test_board_programs_api.py`` run-now test) can leave behind — this
    module's idle-trigger tests need the roadmap dedup gate genuinely open.
    Purge before every test in this file."""
    await db_session.execute(
        delete(SystemSettingTable).where(SystemSettingTable.key.like("board_program.%"))
    )
    await db_session.execute(delete(BoardProgramCycleTable))
    await db_session.execute(
        update(TaskTable)
        .where(
            TaskTable.source.in_([ROADMAP_SOURCE, X_FEATURE_EXPLORATION_SOURCE]),
            TaskTable.status.notin_([TS.COMPLETED, TS.CANCELLED]),
        )
        .values(status=TS.CANCELLED)
    )
    await db_session.commit()


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


# --------------------------------------------------------------------------- #
# Task 6: idle -> roadmap Board Program trigger (real DB — BoardProgramEngine
# dedup is what makes the second tick a no-op, so a fully-mocked session
# can't exercise it; see test_board_program_engine.py for the engine's own
# isolated trigger/dedup coverage).
# --------------------------------------------------------------------------- #

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
PO_UUID = _foundation.AGENTS["product-owner"].uuid
SLUG = "roboco"
ONE = 1


async def _seed_roadmap_fixture(session: AsyncSession) -> None:
    for uuid, slug, role, team in (
        (SYSTEM_UUID, "system", AgentRole.SYSTEM, None),
        (PO_UUID, "product-owner", AgentRole.PRODUCT_OWNER, Team.BOARD),
    ):
        if await session.get(AgentTable, uuid) is None:
            session.add(
                AgentTable(
                    id=uuid,
                    name=slug,
                    slug=slug,
                    role=role,
                    team=team,
                    status=AgentStatus.ACTIVE,
                    model_config={},
                    system_prompt="x",
                    capabilities=[],
                    permissions={},
                    metrics={},
                )
            )
    await session.flush()
    session.add(
        ProjectTable(
            name="RoboCo",
            slug=SLUG,
            git_url="https://github.com/x/roboco.git",
            default_branch="master",
            protected_branches=["master"],
            assigned_cell=Team.BACKEND,
            created_by=SYSTEM_UUID,
            is_active=True,
        )
    )
    await session.flush()


def _mock_idle_assessment(monkeypatch: pytest.MonkeyPatch) -> None:
    task_svc = MagicMock()
    task_svc.list_in_progress_or_claimed = AsyncMock(return_value=[])
    task_svc.list_long_running_blocked = AsyncMock(return_value=[])
    monkeypatch.setattr(se_module, "get_task_service", lambda _s: task_svc)
    goals_svc = MagicMock()
    goals_svc.get = AsyncMock(return_value=_GOALS_WITH_DIRECTION)
    monkeypatch.setattr(se_module, "get_company_goals_service", lambda _s: goals_svc)


@pytest.mark.asyncio
async def test_idle_triggers_roadmap_cycle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_roadmap_fixture(db_session)
    monkeypatch.setattr(se_module.settings, "strategy_engine_enabled", True)
    monkeypatch.setattr(se_module.settings, "roadmap_engine_enabled", True)
    monkeypatch.setattr(se_module.settings, "self_heal_project_slug", SLUG)
    _mock_idle_assessment(monkeypatch)
    notifier = MagicMock()
    notifier.send_ack_notification = AsyncMock()
    monkeypatch.setattr(se_module, "NotificationService", lambda: notifier)

    eng = StrategyEngine(db_session)
    await eng.run_cycle()

    open_cycles = await get_task_service(db_session).list_open_roadmap_cycles()
    assert len(open_cycles) == ONE
    body = notifier.send_ack_notification.call_args.kwargs["body"]
    assert "roadmap exploration cycle was opened" in body


@pytest.mark.asyncio
async def test_idle_triggers_roadmap_cycle_armed_via_settings_store_only(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The roadmap program armed ONLY through the settings-store key (the
    legacy ``roadmap_engine_enabled`` flag left at its False default) still
    reaches origination through the full chain: strategy engine ->
    ``BoardProgramEngine.open_program_cycle`` -> ``program_armed``."""
    await _seed_roadmap_fixture(db_session)
    monkeypatch.setattr(se_module.settings, "strategy_engine_enabled", True)
    monkeypatch.setattr(se_module.settings, "self_heal_project_slug", SLUG)
    db_session.add(
        SystemSettingTable(key="board_program.roadmap.enabled", value="true")
    )
    await db_session.flush()
    _mock_idle_assessment(monkeypatch)
    notifier = MagicMock()
    notifier.send_ack_notification = AsyncMock()
    monkeypatch.setattr(se_module, "NotificationService", lambda: notifier)

    eng = StrategyEngine(db_session)
    await eng.run_cycle()

    open_cycles = await get_task_service(db_session).list_open_roadmap_cycles()
    assert len(open_cycles) == ONE
    body = notifier.send_ack_notification.call_args.kwargs["body"]
    assert "roadmap exploration cycle was opened" in body


@pytest.mark.asyncio
async def test_idle_second_tick_is_a_dedup_noop(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed_roadmap_fixture(db_session)
    monkeypatch.setattr(se_module.settings, "strategy_engine_enabled", True)
    monkeypatch.setattr(se_module.settings, "roadmap_engine_enabled", True)
    monkeypatch.setattr(se_module.settings, "self_heal_project_slug", SLUG)
    _mock_idle_assessment(monkeypatch)
    notifier = MagicMock()
    notifier.send_ack_notification = AsyncMock()
    monkeypatch.setattr(se_module, "NotificationService", lambda: notifier)

    eng = StrategyEngine(db_session)
    await eng.run_cycle()
    await eng.run_cycle()

    open_cycles = await get_task_service(db_session).list_open_roadmap_cycles()
    assert len(open_cycles) == ONE
    second_body = notifier.send_ack_notification.call_args.kwargs["body"]
    assert "already open" in second_body
