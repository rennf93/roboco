"""roboco.services.strategy_engine — assessment + notify (dormant by default)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from roboco.db.tables import (
    AgentTable,
    AuditLogTable,
    BoardProgramCycleTable,
    ProjectTable,
    SystemSettingTable,
    TaskTable,
)
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.content import markers
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    BlockerResolverType,
    TaskType,
    Team,
)
from roboco.models.base import TaskStatus as TS
from roboco.services import strategy_engine as se_module
from roboco.services.strategy_engine import StrategyEngine
from roboco.services.task import (
    CORONER_SOURCE,
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
            TaskTable.source.in_(
                [ROADMAP_SOURCE, X_FEATURE_EXPLORATION_SOURCE, CORONER_SOURCE]
            ),
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


# --------------------------------------------------------------------------- #
# Task: stranded_blocked -> coroner autopsy trigger (real DB — the Coroner
# dedup + arming gate are what make the second tick / disarmed case a no-op,
# so a fully-mocked session can't exercise them).
# --------------------------------------------------------------------------- #

AUDITOR_UUID = _foundation.AGENTS["auditor"].uuid


async def _seed_coroner_fixture(session: AsyncSession) -> UUID:
    """Seed system + auditor agents + a project for coroner tests.

    Returns the project UUID for seeding blocked tasks against.
    """
    for uuid, slug, role, team in (
        (SYSTEM_UUID, "system", AgentRole.SYSTEM, None),
        (AUDITOR_UUID, "auditor", AgentRole.AUDITOR, Team.BOARD),
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
    project = ProjectTable(
        name="RoboCo",
        slug=SLUG,
        git_url="https://github.com/x/roboco.git",
        default_branch="master",
        protected_branches=["master"],
        assigned_cell=Team.BACKEND,
        created_by=SYSTEM_UUID,
        is_active=True,
    )
    session.add(project)
    await session.flush()
    return UUID(str(project.id))


async def _seed_blocked_task(
    session: AsyncSession,
    project_id: UUID,
    *,
    title: str = "stranded-seed-task",
    age_minutes: int = 240,
    dev_notes: str | None = None,
) -> UUID:
    """Seed a BLOCKED task with ``updated_at`` in the past so
    ``list_long_running_blocked`` finds it past the threshold."""
    task = TaskTable(
        id=uuid4(),
        title=title,
        description="seeded blocked task for coroner trigger test",
        acceptance_criteria=["seeded"],
        status=TS.BLOCKED,
        priority=2,
        task_type=TaskType.CODE,
        team=Team.BACKEND,
        created_by=SYSTEM_UUID,
        project_id=project_id,
        blocker_resolver_type=BlockerResolverType.HUMAN,
        revision_count=0,
        dev_notes=dev_notes,
        updated_at=datetime.now(UTC) - timedelta(minutes=age_minutes),
    )
    session.add(task)
    await session.flush()
    return UUID(str(task.id))


def _mock_stranded_assessment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock goals to empty (no direction → no idle observation); let the real
    task service find the seeded blocked task (no mock on get_task_service)."""
    goals_svc = MagicMock()
    goals_svc.get = AsyncMock(return_value=_GOALS_EMPTY)
    monkeypatch.setattr(se_module, "get_company_goals_service", lambda _s: goals_svc)


@pytest.mark.asyncio
async def test_stranded_triggers_coroner_cycle(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = await _seed_coroner_fixture(db_session)
    await _seed_blocked_task(db_session, project_id)
    monkeypatch.setattr(se_module.settings, "strategy_engine_enabled", True)
    monkeypatch.setattr(se_module.settings, "strategy_stranded_blocked_minutes", 1)
    monkeypatch.setattr(se_module.settings, "self_heal_project_slug", SLUG)
    db_session.add(
        SystemSettingTable(key="board_program.coroner.enabled", value="true")
    )
    await db_session.flush()
    _mock_stranded_assessment(monkeypatch)
    notifier = MagicMock()
    notifier.send_ack_notification = AsyncMock()
    monkeypatch.setattr(se_module, "NotificationService", lambda: notifier)

    eng = StrategyEngine(db_session)
    await eng.run_cycle()

    open_cycles = await get_task_service(db_session).list_open_coroner_cycles()
    assert len(open_cycles) == ONE
    body = notifier.send_ack_notification.call_args.kwargs["body"]
    assert "Coroner autopsy" in body
    assert "stranded-seed-task" in body


_MIN_BLOCKED_MINUTES_FROM_AUDIT = 100


@pytest.mark.asyncio
async def test_stranded_time_blocked_uses_audit_event_not_updated_at(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """time_blocked is derived from the latest task.blocked audit
    transition, not the task's updated_at — updated_at moves on any later
    touch while the task sits blocked and would otherwise wildly
    underestimate how long it's actually been stuck. Also asserts
    block_reason (parsed from the real dev_notes blocker note, not just
    blocker_resolver_type) and escalation_history (real task.escalated
    audit rows, not just the blocked-event count) — both were write-only
    before this test covered them."""
    project_id = await _seed_coroner_fixture(db_session)
    blocked_note = (
        "[BLOCKED - QUESTION]\n"
        "Reason: need the staging DB credentials\n"
        "What's needed: the ops team to rotate and share a token"
    )
    task_id = await _seed_blocked_task(
        db_session, project_id, age_minutes=5, dev_notes=blocked_note
    )
    # The real blockage started hours before the last updated_at touch.
    db_session.add(
        AuditLogTable(
            id=uuid4(),
            event_type="task.blocked",
            target_type="task",
            target_id=task_id,
            severity="info",
            details={},
            timestamp=datetime.now(UTC) - timedelta(hours=6),
        )
    )
    db_session.add(
        AuditLogTable(
            id=uuid4(),
            event_type="task.escalated",
            target_type="task",
            target_id=task_id,
            severity="info",
            details={},
            timestamp=datetime.now(UTC) - timedelta(hours=5),
        )
    )
    monkeypatch.setattr(se_module.settings, "strategy_engine_enabled", True)
    monkeypatch.setattr(se_module.settings, "strategy_stranded_blocked_minutes", 1)
    monkeypatch.setattr(se_module.settings, "self_heal_project_slug", SLUG)
    db_session.add(
        SystemSettingTable(key="board_program.coroner.enabled", value="true")
    )
    await db_session.flush()
    _mock_stranded_assessment(monkeypatch)
    notifier = MagicMock()
    notifier.send_ack_notification = AsyncMock()
    monkeypatch.setattr(se_module, "NotificationService", lambda: notifier)

    eng = StrategyEngine(db_session)
    await eng.run_cycle()

    open_cycles = await get_task_service(db_session).list_open_coroner_cycles()
    assert len(open_cycles) == ONE
    incident_ref = markers.get_coroner_incident(open_cycles[0])
    assert incident_ref is not None
    minutes = int(incident_ref["time_blocked"].split()[0])
    # ~360 minutes (the audit event, 6h ago), never ~5 (updated_at's age).
    assert minutes > _MIN_BLOCKED_MINUTES_FROM_AUDIT
    # block_reason is the real dev_notes text, not blocker_resolver_type
    # ("human").
    assert "staging DB credentials" in incident_ref["block_reason"]
    assert "rotate and share a token" in incident_ref["block_reason"]
    assert "human" not in incident_ref["block_reason"].lower()
    # escalation_history reports the seeded task.escalated row, not just
    # the blocked-event count.
    assert "escalated 1 time" in incident_ref["escalation_history"]


@pytest.mark.asyncio
async def test_stranded_second_tick_is_a_dedup_noop(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = await _seed_coroner_fixture(db_session)
    await _seed_blocked_task(db_session, project_id)
    monkeypatch.setattr(se_module.settings, "strategy_engine_enabled", True)
    monkeypatch.setattr(se_module.settings, "strategy_stranded_blocked_minutes", 1)
    monkeypatch.setattr(se_module.settings, "self_heal_project_slug", SLUG)
    db_session.add(
        SystemSettingTable(key="board_program.coroner.enabled", value="true")
    )
    await db_session.flush()
    _mock_stranded_assessment(monkeypatch)
    notifier = MagicMock()
    notifier.send_ack_notification = AsyncMock()
    monkeypatch.setattr(se_module, "NotificationService", lambda: notifier)

    eng = StrategyEngine(db_session)
    await eng.run_cycle()
    await eng.run_cycle()

    open_cycles = await get_task_service(db_session).list_open_coroner_cycles()
    assert len(open_cycles) == ONE
    second_body = notifier.send_ack_notification.call_args.kwargs["body"]
    assert "not opened" in second_body
    # The dedup no-op still names WHICH task is stranded, not just that
    # something was skipped.
    assert "stranded-seed-task" in second_body


@pytest.mark.asyncio
async def test_stranded_coroner_disarmed_noop(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    project_id = await _seed_coroner_fixture(db_session)
    await _seed_blocked_task(db_session, project_id)
    monkeypatch.setattr(se_module.settings, "strategy_engine_enabled", True)
    monkeypatch.setattr(se_module.settings, "strategy_stranded_blocked_minutes", 1)
    monkeypatch.setattr(se_module.settings, "self_heal_project_slug", SLUG)
    # Coroner program NOT armed — no settings-store row, no legacy flag.
    _mock_stranded_assessment(monkeypatch)
    notifier = MagicMock()
    notifier.send_ack_notification = AsyncMock()
    monkeypatch.setattr(se_module, "NotificationService", lambda: notifier)

    eng = StrategyEngine(db_session)
    await eng.run_cycle()

    open_cycles = await get_task_service(db_session).list_open_coroner_cycles()
    assert len(open_cycles) == 0
    body = notifier.send_ack_notification.call_args.kwargs["body"]
    assert "not opened" in body
    # The disarmed no-op still names WHICH task is stranded.
    assert "stranded-seed-task" in body
