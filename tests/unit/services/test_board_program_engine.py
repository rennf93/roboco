"""BoardProgramEngine: trigger/dedup/originate/LEARN over the registry.

Mirrors test_roadmap_engine.py's seeding + real-Postgres style, but swaps in
fake originators (monkeypatched into board_programs._ORIGINATORS) so this
suite tests the ENGINE's own dedup/cron/LEARN logic in isolation from
RoadmapEngine/XEngine's own internal guards (covered by their own suites).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest
import pytest_asyncio
from roboco.config import settings as cfg
from roboco.db.tables import (
    AgentTable,
    BoardProgramCycleTable,
    ProjectTable,
    SystemSettingTable,
    TaskTable,
)
from roboco.foundation import identity as _foundation
from roboco.foundation.policy.board_programs import (
    PROGRAMS,
    WEEK_SECONDS,
    BoardProgram,
    TriggerKind,
)
from roboco.models.base import (
    AgentRole,
    AgentStatus,
    Complexity,
    TaskNature,
    TaskType,
    Team,
)
from roboco.models.base import (
    TaskStatus as TS,
)
from roboco.services import board_programs as bp_module
from roboco.services.board_programs import BoardProgramEngine
from roboco.services.task import (
    PERISCOPE_SOURCE,
    PEST_CONTROL_SOURCE,
    ROADMAP_SOURCE,
    X_FEATURE_EXPLORATION_SOURCE,
    TaskCreateRequest,
    get_task_service,
)
from sqlalchemy import delete, select, update

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

SYSTEM_UUID = _foundation.AGENTS["system"].uuid
PO_UUID = _foundation.AGENTS["product-owner"].uuid
SLUG = "roboco"
ONE = 1
TWO = 2


@pytest_asyncio.fixture(autouse=True)
async def _purge_board_program_pollution(db_session: AsyncSession) -> None:
    """Board Program state (settings-store overrides, ledger rows, open
    roadmap/x_feature exploration tasks) is shared, cross-test-persistent DB
    state — this module's own tests write it mid-test, and the write-route
    integration suite (``test_board_programs_api.py``'s run-now, which
    commits) can leave it behind too. Purge before every test in this file
    so a leftover row never reads back as a false "already open"/"already
    armed" state or collides on a settings-store primary key."""
    await db_session.execute(
        delete(SystemSettingTable).where(SystemSettingTable.key.like("board_program.%"))
    )
    await db_session.execute(delete(BoardProgramCycleTable))
    await db_session.execute(
        update(TaskTable)
        .where(
            TaskTable.source.in_(
                [
                    ROADMAP_SOURCE,
                    X_FEATURE_EXPLORATION_SOURCE,
                    PEST_CONTROL_SOURCE,
                    PERISCOPE_SOURCE,
                ]
            ),
            TaskTable.status.notin_([TS.COMPLETED, TS.CANCELLED]),
        )
        .values(status=TS.CANCELLED)
    )
    await db_session.commit()


async def _seed(session: AsyncSession) -> None:
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


async def _make_exploration(
    session: AsyncSession, *, source: str, status: TS = TS.PENDING
) -> TaskTable:
    project = (
        await session.execute(select(ProjectTable).where(ProjectTable.slug == SLUG))
    ).scalar_one()
    task = await get_task_service(session).create(
        TaskCreateRequest(
            title="exploration cycle",
            description="x",
            acceptance_criteria=["propose once"],
            team=Team.BOARD,
            assigned_to=PO_UUID,
            created_by=SYSTEM_UUID,
            task_type=TaskType.ADMINISTRATIVE,
            nature=TaskNature.NON_TECHNICAL,
            estimated_complexity=Complexity.LOW,
            project_id=cast("UUID", project.id),
            status=TS.PENDING,
            source=source,
            confirmed_by_human=False,
        )
    )
    if status != TS.PENDING:
        task.status = status
    await session.flush()
    return task


def _fake_originator(
    holder: dict[str, TaskTable | None],
) -> Callable[[AsyncSession], Awaitable[TaskTable | None]]:
    async def _originate(_session: AsyncSession) -> TaskTable | None:
        return holder["task"]

    return _originate


def _patch_roadmap_originator(
    monkeypatch: pytest.MonkeyPatch, holder: dict[str, TaskTable | None]
) -> None:
    monkeypatch.setitem(bp_module._ORIGINATORS, "roadmap", _fake_originator(holder))


@pytest.mark.asyncio
async def test_disabled_program_never_originates(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setattr(cfg, "roadmap_engine_enabled", False)
    holder: dict[str, TaskTable | None] = {"task": None}
    _patch_roadmap_originator(monkeypatch, holder)
    engine = BoardProgramEngine(db_session)
    opened = await engine.run_due_programs()
    assert "roadmap" not in opened


@pytest.mark.asyncio
async def test_dormant_with_no_settings_store_rows_and_legacy_flags_off(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No settings-store overrides + both legacy boot flags False: a tick
    originates nothing and writes ZERO ledger rows — the guarantee the
    deleted per-engine dormant-loop tests covered, now at the engine layer
    every program's arming decision routes through."""
    await _seed(db_session)
    monkeypatch.setattr(cfg, "roadmap_engine_enabled", False)
    monkeypatch.setattr(cfg, "x_engine_enabled", False)
    monkeypatch.setattr(cfg, "x_feature_spotlight_enabled", False)
    engine = BoardProgramEngine(db_session)
    opened = await engine.run_due_programs()
    assert opened == []

    rows = (await db_session.execute(select(BoardProgramCycleTable))).scalars().all()
    assert rows == []


@pytest.mark.asyncio
async def test_open_cycle_blocks_reorigination(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setattr(cfg, "roadmap_engine_enabled", True)
    task = await _make_exploration(db_session, source=ROADMAP_SOURCE)
    db_session.add(
        BoardProgramCycleTable(
            program_key="roadmap",
            exploration_task_id=task.id,
            opened_at=datetime.now(UTC),
        )
    )
    await db_session.flush()
    holder: dict[str, TaskTable | None] = {"task": None}
    _patch_roadmap_originator(monkeypatch, holder)
    engine = BoardProgramEngine(db_session)
    opened = await engine.run_due_programs()
    assert "roadmap" not in opened


@pytest.mark.asyncio
async def test_due_program_originates_and_opens_cycle_row(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setattr(cfg, "roadmap_engine_enabled", True)
    new_task = await _make_exploration(db_session, source=ROADMAP_SOURCE)
    holder: dict[str, TaskTable | None] = {"task": new_task}
    _patch_roadmap_originator(monkeypatch, holder)
    engine = BoardProgramEngine(db_session)
    opened = await engine.run_due_programs()
    assert opened == ["roadmap"]

    rows = (
        (
            await db_session.execute(
                select(BoardProgramCycleTable).where(
                    BoardProgramCycleTable.program_key == "roadmap"
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == ONE
    assert rows[0].exploration_task_id == new_task.id
    assert rows[0].closed_at is None


@pytest.mark.asyncio
async def test_closed_cycle_past_interval_allows_reorigination(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setattr(cfg, "roadmap_engine_enabled", True)
    monkeypatch.setattr(cfg, "roadmap_interval_seconds", 300)
    old_task = await _make_exploration(
        db_session, source=ROADMAP_SOURCE, status=TS.COMPLETED
    )
    db_session.add(
        BoardProgramCycleTable(
            program_key="roadmap",
            exploration_task_id=old_task.id,
            opened_at=datetime.now(UTC) - timedelta(seconds=301),
            closed_at=datetime.now(UTC) - timedelta(seconds=200),
        )
    )
    await db_session.flush()
    new_task = await _make_exploration(db_session, source=ROADMAP_SOURCE)
    holder: dict[str, TaskTable | None] = {"task": new_task}
    _patch_roadmap_originator(monkeypatch, holder)
    engine = BoardProgramEngine(db_session)
    opened = await engine.run_due_programs()
    assert opened == ["roadmap"]


@pytest.mark.asyncio
async def test_auto_closes_open_row_once_task_goes_terminal(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale open row whose exploration task already went terminal is
    reconciled (auto-closed) rather than permanently blocking dedup."""
    await _seed(db_session)
    monkeypatch.setattr(cfg, "roadmap_engine_enabled", True)
    monkeypatch.setattr(cfg, "roadmap_interval_seconds", 1)
    stale_task = await _make_exploration(
        db_session, source=ROADMAP_SOURCE, status=TS.COMPLETED
    )
    db_session.add(
        BoardProgramCycleTable(
            program_key="roadmap",
            exploration_task_id=stale_task.id,
            opened_at=datetime.now(UTC) - timedelta(seconds=5),
        )
    )
    await db_session.flush()
    new_task = await _make_exploration(db_session, source=ROADMAP_SOURCE)
    holder: dict[str, TaskTable | None] = {"task": new_task}
    _patch_roadmap_originator(monkeypatch, holder)
    engine = BoardProgramEngine(db_session)
    opened = await engine.run_due_programs()
    assert opened == ["roadmap"]

    rows = (
        (
            await db_session.execute(
                select(BoardProgramCycleTable)
                .where(BoardProgramCycleTable.program_key == "roadmap")
                .order_by(BoardProgramCycleTable.opened_at)
            )
        )
        .scalars()
        .all()
    )
    assert len(rows) == TWO
    assert rows[0].closed_at is not None  # reconciled


@pytest.mark.asyncio
async def test_record_decision_bumps_counters_and_closes_when_done(
    db_session: AsyncSession,
) -> None:
    await _seed(db_session)
    task = await _make_exploration(
        db_session, source=ROADMAP_SOURCE, status=TS.COMPLETED
    )
    db_session.add(
        BoardProgramCycleTable(
            program_key="roadmap",
            exploration_task_id=task.id,
            opened_at=datetime.now(UTC),
        )
    )
    await db_session.flush()
    engine = BoardProgramEngine(db_session)
    await engine.record_decision("roadmap", "item-1", "approved")
    await engine.record_decision("roadmap", "item-2", "rejected", reason="not now")

    row = await engine._latest_cycle("roadmap")
    assert row is not None
    assert row.items_proposed == TWO
    assert row.items_approved == 1
    assert row.items_rejected == 1
    assert row.closed_at is not None  # exploration task was already terminal
    assert {
        "item_ref": "item-1",
        "verdict": "approved",
        "reason": None,
    } in row.decisions


@pytest.mark.asyncio
async def test_record_decision_targets_named_exploration_over_most_recent(
    db_session: AsyncSession,
) -> None:
    """Two cycle rows exist for "roadmap": the FIRST auto-closed via a
    terminal exploration task with items still undecided (the admin-cancel
    edge), the SECOND opened after it and is the most-recent row. A decision
    carrying the FIRST task's id must land on the FIRST row, not silently
    fall through to the most-recent-cycle fallback."""
    await _seed(db_session)
    first_task = await _make_exploration(
        db_session, source=ROADMAP_SOURCE, status=TS.CANCELLED
    )
    db_session.add(
        BoardProgramCycleTable(
            program_key="roadmap",
            exploration_task_id=first_task.id,
            opened_at=datetime.now(UTC) - timedelta(hours=2),
        )
    )
    second_task = await _make_exploration(db_session, source=ROADMAP_SOURCE)
    db_session.add(
        BoardProgramCycleTable(
            program_key="roadmap",
            exploration_task_id=second_task.id,
            opened_at=datetime.now(UTC) - timedelta(hours=1),
        )
    )
    await db_session.flush()

    engine = BoardProgramEngine(db_session)
    await engine.record_decision(
        "roadmap",
        "item-1",
        "approved",
        exploration_task_id=cast("UUID", first_task.id),
    )

    first_row = await engine._cycle_for_exploration(
        "roadmap", cast("UUID", first_task.id)
    )
    second_row = await engine._cycle_for_exploration(
        "roadmap", cast("UUID", second_task.id)
    )
    assert first_row is not None
    assert second_row is not None
    assert first_row.items_proposed == ONE
    assert first_row.items_approved == ONE
    assert {
        "item_ref": "item-1",
        "verdict": "approved",
        "reason": None,
    } in first_row.decisions
    assert first_row.closed_at is not None  # reconciled: task was terminal
    assert second_row.items_proposed == 0
    assert second_row.decisions == []


@pytest.mark.asyncio
async def test_prior_cycle_context_renders_rejections_with_reasons(
    db_session: AsyncSession,
) -> None:
    await _seed(db_session)
    assert await BoardProgramEngine(db_session).prior_cycle_context("roadmap") == ""

    task = await _make_exploration(
        db_session, source=ROADMAP_SOURCE, status=TS.COMPLETED
    )
    db_session.add(
        BoardProgramCycleTable(
            program_key="roadmap",
            exploration_task_id=task.id,
            opened_at=datetime.now(UTC),
            closed_at=datetime.now(UTC),
            items_proposed=2,
            items_approved=1,
            items_rejected=1,
            decisions=[
                {"item_ref": "item-1", "verdict": "approved", "reason": None},
                {"item_ref": "item-2", "verdict": "rejected", "reason": "too risky"},
            ],
        )
    )
    await db_session.flush()

    context = await BoardProgramEngine(db_session).prior_cycle_context("roadmap")
    assert "proposed 2, approved 1" in context
    assert "item-2 — too risky" in context


def test_originators_cover_exactly_the_registry() -> None:
    assert set(bp_module._ORIGINATORS) == set(PROGRAMS)


def test_program_sources_match_service_layer_constants() -> None:
    assert PROGRAMS["roadmap"].source == ROADMAP_SOURCE
    assert PROGRAMS["x_feature"].source == X_FEATURE_EXPLORATION_SOURCE
    assert PROGRAMS["pest_control"].source == PEST_CONTROL_SOURCE
    assert PROGRAMS["periscope"].source == PERISCOPE_SOURCE


# ---------------------------------------------------------------------------
# Task 6b: per-project program scoping
# ---------------------------------------------------------------------------

_PEST_CONTROL = BoardProgram(
    key="pest_control",
    role="product_owner",
    trigger=TriggerKind.CRON,
    source="board_pest_control",
    default_interval_seconds=1,
    scope="project",
)


def _arm_setting(session: AsyncSession, key: str) -> None:
    """Bypass ``SettingsService.set``'s key allowlist (a project-scoped test
    program is never a real writable key) and write the raw row directly —
    ``get_bool`` only reads it, it never validates."""
    session.add(SystemSettingTable(key=key, value="true"))


@pytest.mark.asyncio
async def test_opted_in_projects_filters_by_project_participates(
    db_session: AsyncSession,
) -> None:
    await _seed(db_session)
    opted_in = ProjectTable(
        name="Opted In",
        slug="opted-in-proj",
        git_url="https://github.com/x/opted-in.git",
        default_branch="master",
        protected_branches=["master"],
        assigned_cell=Team.BACKEND,
        created_by=SYSTEM_UUID,
        is_active=True,
        board_programs=["pest_control"],
    )
    db_session.add(opted_in)
    await db_session.flush()

    engine = BoardProgramEngine(db_session)
    projects = await engine.opted_in_projects(_PEST_CONTROL)
    # SLUG ("roboco", seeded by _seed) never opted in — only the new project.
    assert {p.slug for p in projects} == {"opted-in-proj"}


@pytest.mark.asyncio
async def test_run_due_programs_skips_project_scoped_program_with_no_opt_in(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setitem(bp_module.PROGRAMS, "pest_control", _PEST_CONTROL)
    _arm_setting(db_session, "board_program.pest_control.enabled")
    holder: dict[str, TaskTable | None] = {"task": None}
    monkeypatch.setitem(
        bp_module._ORIGINATORS, "pest_control", _fake_originator(holder)
    )
    await db_session.flush()

    engine = BoardProgramEngine(db_session)
    opened = await engine.run_due_programs()
    assert "pest_control" not in opened

    rows = (
        (
            await db_session.execute(
                select(BoardProgramCycleTable).where(
                    BoardProgramCycleTable.program_key == "pest_control"
                )
            )
        )
        .scalars()
        .all()
    )
    assert rows == []


@pytest.mark.asyncio
async def test_open_program_cycle_returns_none_with_no_project_opted_in(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    monkeypatch.setitem(bp_module.PROGRAMS, "pest_control", _PEST_CONTROL)
    _arm_setting(db_session, "board_program.pest_control.enabled")
    await db_session.flush()

    engine = BoardProgramEngine(db_session)
    assert await engine.open_program_cycle("pest_control") is None


@pytest.mark.asyncio
async def test_run_due_programs_originates_project_scoped_program_with_opt_in(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    project = (
        await db_session.execute(select(ProjectTable).where(ProjectTable.slug == SLUG))
    ).scalar_one()
    project.board_programs = ["pest_control"]
    monkeypatch.setitem(bp_module.PROGRAMS, "pest_control", _PEST_CONTROL)
    _arm_setting(db_session, "board_program.pest_control.enabled")
    new_task = await _make_exploration(db_session, source="board_pest_control")
    holder: dict[str, TaskTable | None] = {"task": new_task}
    monkeypatch.setitem(
        bp_module._ORIGINATORS, "pest_control", _fake_originator(holder)
    )
    await db_session.flush()

    engine = BoardProgramEngine(db_session)
    opened = await engine.run_due_programs()
    assert opened == ["pest_control"]


# ---------------------------------------------------------------------------
# Pest Control's metric-predicate accelerator (spec §4: "weekly cron OR
# rework-rate spike") — the predicate opens a cycle off-schedule, still gated
# by enabled + scope + dedup exactly like the cron path.
# ---------------------------------------------------------------------------

# A far-future cron cadence so the cron pass never fires within these tests —
# only the metric predicate can open the cycle.
_NEVER_DUE = BoardProgram(
    key="pest_control",
    role="product_owner",
    trigger=TriggerKind.CRON,
    source="board_pest_control",
    default_interval_seconds=WEEK_SECONDS * 100,
    scope="project",
)


async def _seed_recently_closed_cycle(session: AsyncSession) -> None:
    """A CLOSED ledger row opened just now — makes the CRON pass genuinely
    NOT due (recent + a huge interval) so a test can isolate the metric-
    predicate path. Needs no linked task: ``_dedup_state`` never runs the
    auto-close reconciliation on a row whose ``closed_at`` is already set."""
    session.add(
        BoardProgramCycleTable(
            program_key="pest_control",
            exploration_task_id=None,
            opened_at=datetime.now(UTC),
            closed_at=datetime.now(UTC),
        )
    )
    await session.flush()


@pytest.mark.asyncio
async def test_metric_predicate_opens_cycle_off_schedule_when_it_fires(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    project = (
        await db_session.execute(select(ProjectTable).where(ProjectTable.slug == SLUG))
    ).scalar_one()
    project.board_programs = ["pest_control"]
    monkeypatch.setitem(bp_module.PROGRAMS, "pest_control", _NEVER_DUE)
    _arm_setting(db_session, "board_program.pest_control.enabled")
    await _seed_recently_closed_cycle(db_session)
    new_task = await _make_exploration(db_session, source="board_pest_control")
    holder: dict[str, TaskTable | None] = {"task": new_task}
    monkeypatch.setitem(
        bp_module._ORIGINATORS, "pest_control", _fake_originator(holder)
    )
    monkeypatch.setitem(
        bp_module._METRIC_PREDICATES,
        "pest_control",
        _fake_predicate(True),
    )
    await db_session.flush()

    engine = BoardProgramEngine(db_session)
    opened = await engine.run_due_programs()
    assert opened == ["pest_control"]


@pytest.mark.asyncio
async def test_metric_predicate_below_threshold_opens_nothing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _seed(db_session)
    project = (
        await db_session.execute(select(ProjectTable).where(ProjectTable.slug == SLUG))
    ).scalar_one()
    project.board_programs = ["pest_control"]
    monkeypatch.setitem(bp_module.PROGRAMS, "pest_control", _NEVER_DUE)
    _arm_setting(db_session, "board_program.pest_control.enabled")
    await _seed_recently_closed_cycle(db_session)
    monkeypatch.setitem(
        bp_module._METRIC_PREDICATES,
        "pest_control",
        _fake_predicate(False),
    )
    await db_session.flush()

    engine = BoardProgramEngine(db_session)
    opened = await engine.run_due_programs()
    assert opened == []


@pytest.mark.asyncio
async def test_metric_predicate_never_consulted_when_disabled(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A disabled program's predicate must never even run (cheap, isolated —
    no wasted MetricsService query on a dormant program)."""
    await _seed(db_session)
    monkeypatch.setitem(bp_module.PROGRAMS, "pest_control", _NEVER_DUE)
    called = {"n": 0}

    async def _boom(_session: AsyncSession) -> bool:
        called["n"] += 1
        raise AssertionError("predicate must not run while disabled")

    monkeypatch.setitem(bp_module._METRIC_PREDICATES, "pest_control", _boom)
    await db_session.flush()

    engine = BoardProgramEngine(db_session)
    opened = await engine.run_due_programs()
    assert opened == []
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_metric_predicate_never_evaluated_when_dedup_blocked(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An already-open cycle blocks the metric predicate from ever running —
    the cheap dedup gate must run BEFORE the (8-11 query) rework-rate check,
    so an open cycle costs the metrics service nothing on every tick."""
    await _seed(db_session)
    project = (
        await db_session.execute(select(ProjectTable).where(ProjectTable.slug == SLUG))
    ).scalar_one()
    project.board_programs = ["pest_control"]
    monkeypatch.setitem(bp_module.PROGRAMS, "pest_control", _NEVER_DUE)
    _arm_setting(db_session, "board_program.pest_control.enabled")
    open_task = await _make_exploration(db_session, source="board_pest_control")
    db_session.add(
        BoardProgramCycleTable(
            program_key="pest_control",
            exploration_task_id=open_task.id,
            opened_at=datetime.now(UTC),
        )
    )
    called = {"n": 0}

    async def _boom(_session: AsyncSession) -> bool:
        called["n"] += 1
        raise AssertionError("predicate must not run while dedup-blocked")

    monkeypatch.setitem(bp_module._METRIC_PREDICATES, "pest_control", _boom)
    await db_session.flush()

    engine = BoardProgramEngine(db_session)
    opened = await engine.run_due_programs()
    assert opened == []
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_metric_predicate_never_evaluated_when_scope_empty(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No project opted into pest_control — the cheap scope gate blocks the
    metric predicate from ever running (no project ever opts SLUG in here,
    unlike the sibling tests)."""
    await _seed(db_session)
    monkeypatch.setitem(bp_module.PROGRAMS, "pest_control", _NEVER_DUE)
    _arm_setting(db_session, "board_program.pest_control.enabled")
    called = {"n": 0}

    async def _boom(_session: AsyncSession) -> bool:
        called["n"] += 1
        raise AssertionError("predicate must not run with no project opted in")

    monkeypatch.setitem(bp_module._METRIC_PREDICATES, "pest_control", _boom)
    await db_session.flush()

    engine = BoardProgramEngine(db_session)
    opened = await engine.run_due_programs()
    assert opened == []
    assert called["n"] == 0


@pytest.mark.asyncio
async def test_real_rework_predicate_fires_above_threshold(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercises the REAL predicate (not a fake) against MetricsService's
    rework rate — proves the threshold wiring, not just the engine seam."""
    monkeypatch.setattr(cfg, "pest_rework_threshold", 0.3)
    result = await bp_module._pest_control_rework_spike(db_session)
    assert result is False  # no completed/reworked tasks seeded -> rate 0.0


def _fake_predicate(
    verdict: bool,
) -> Callable[[AsyncSession], Awaitable[bool]]:
    async def _predicate(_session: AsyncSession) -> bool:
        return verdict

    return _predicate
