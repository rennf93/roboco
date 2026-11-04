"""BoardProgramCadenceService: cost per cycle next to proposal approval rate.

Real-Postgres style, like test_board_program_engine.py: cycles are seeded
against a real exploration task and cost rides real agent_spawn_sessions rows.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
from roboco.db.tables import AgentSpawnSessionTable, BoardProgramCycleTable
from roboco.foundation.policy.board_program_cadence import (
    CadenceLimits,
    decide_slowdown,
)
from roboco.foundation.policy.board_programs import PROGRAMS
from roboco.services.board_program_cadence import get_board_program_cadence_service
from roboco.services.task import ROADMAP_SOURCE
from sqlalchemy import delete
from tests.unit.services.test_board_program_engine import _make_exploration, _seed

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

KEY = "roadmap"
OTHER = "pest_control"
HALF = 0.5
SOLO_COST = 40.0
MIXED_TOTAL = 60.0
MIXED_MEAN = 30.0
OTHER_COST = 99.0
TWO_CYCLES = 2
MISSING_CYCLES = 3


@pytest_asyncio.fixture(autouse=True)
async def _empty_ledger(db_session: AsyncSession) -> None:
    await db_session.execute(delete(BoardProgramCycleTable))
    await db_session.flush()


def _session_row(task_id: str | None, cost: float | None) -> AgentSpawnSessionTable:
    return AgentSpawnSessionTable(
        agent_slug="product-owner",
        team="board",
        role="product_owner",
        model="opus",
        task_id=task_id,
        estimated_cost_usd=cost,
    )


@pytest.mark.asyncio
async def test_every_program_reported_with_none_rates_when_no_cycles(
    db_session: AsyncSession,
) -> None:
    metrics = await get_board_program_cadence_service(
        db_session
    ).program_cadence_metrics()

    assert set(metrics) == set(PROGRAMS)
    empty = metrics[KEY]
    assert (empty.cycles_counted, empty.cycles_missing_cost) == (0, 0)
    assert empty.total_cost_usd == 0.0
    assert empty.cost_per_cycle_usd is None
    assert empty.approval_rate is None


@pytest.mark.asyncio
async def test_cycle_without_task_is_missing_cost_not_in_cost_mean(
    db_session: AsyncSession,
) -> None:
    db_session.add(
        BoardProgramCycleTable(
            program_key=KEY,
            exploration_task_id=None,
            items_proposed=2,
            items_approved=1,
        )
    )
    await db_session.flush()

    metric = (
        await get_board_program_cadence_service(db_session).program_cadence_metrics()
    )[KEY]

    assert metric.cycles_missing_cost == 1
    assert metric.cycles_counted == 0
    assert metric.cost_per_cycle_usd is None
    assert metric.approval_rate == HALF


@pytest.mark.asyncio
async def test_zero_proposals_gives_none_approval_rate_but_real_cost(
    db_session: AsyncSession,
) -> None:
    await _seed(db_session)
    task = await _make_exploration(db_session, source=ROADMAP_SOURCE)
    db_session.add(BoardProgramCycleTable(program_key=KEY, exploration_task_id=task.id))
    db_session.add(_session_row(str(task.id), SOLO_COST))
    await db_session.flush()

    metric = (
        await get_board_program_cadence_service(db_session).program_cadence_metrics()
    )[KEY]

    assert metric.items_proposed == 0
    assert metric.approval_rate is None
    assert metric.cycles_counted == 1
    assert metric.cost_per_cycle_usd == SOLO_COST


@pytest.mark.asyncio
async def test_mixed_cycles_sum_cost_and_rate_per_program(
    db_session: AsyncSession,
) -> None:
    await _seed(db_session)
    first = await _make_exploration(db_session, source=ROADMAP_SOURCE)
    second = await _make_exploration(db_session, source=ROADMAP_SOURCE)
    other = await _make_exploration(db_session, source=ROADMAP_SOURCE)
    db_session.add_all(
        [
            BoardProgramCycleTable(
                program_key=KEY,
                exploration_task_id=first.id,
                items_proposed=3,
                items_approved=1,
            ),
            BoardProgramCycleTable(
                program_key=KEY,
                exploration_task_id=second.id,
                items_proposed=1,
                items_approved=1,
            ),
            BoardProgramCycleTable(
                program_key=KEY,
                exploration_task_id=None,
                items_proposed=0,
                items_approved=0,
            ),
            BoardProgramCycleTable(
                program_key=OTHER,
                exploration_task_id=other.id,
                items_proposed=4,
                items_approved=0,
            ),
            _session_row(str(first.id), 30.0),
            _session_row(str(first.id), 10.0),
            _session_row(str(first.id), None),
            _session_row(str(second.id), 20.0),
            _session_row(str(other.id), 99.0),
        ]
    )
    await db_session.flush()

    metrics = await get_board_program_cadence_service(
        db_session
    ).program_cadence_metrics()

    main = metrics[KEY]
    assert main.cycles_counted == TWO_CYCLES
    assert main.cycles_missing_cost == 1
    assert main.total_cost_usd == MIXED_TOTAL
    assert main.cost_per_cycle_usd == MIXED_MEAN
    assert (main.items_proposed, main.items_approved) == (4, 2)
    assert main.approval_rate == HALF
    assert metrics[OTHER].total_cost_usd == OTHER_COST
    assert metrics[OTHER].approval_rate == 0


@pytest.mark.asyncio
async def test_approvals_of_missing_cost_cycles_stay_out_of_counted_approvals(
    db_session: AsyncSession,
) -> None:
    await _seed(db_session)
    task = await _make_exploration(db_session, source=ROADMAP_SOURCE)
    db_session.add_all(
        [
            BoardProgramCycleTable(
                program_key=KEY,
                exploration_task_id=task.id,
                items_proposed=2,
                items_approved=0,
            ),
            *(
                BoardProgramCycleTable(
                    program_key=KEY,
                    exploration_task_id=None,
                    items_proposed=1,
                    items_approved=1,
                )
                for _ in range(MISSING_CYCLES)
            ),
            _session_row(str(task.id), 60.0),
        ]
    )
    await db_session.flush()

    metric = (
        await get_board_program_cadence_service(db_session).program_cadence_metrics()
    )[KEY]

    assert metric.cycles_counted == 1
    assert metric.cycles_missing_cost == MISSING_CYCLES
    assert metric.items_approved == MISSING_CYCLES
    assert metric.items_approved_counted == 0
    decision = decide_slowdown(
        cost_per_cycle_usd=metric.cost_per_cycle_usd,
        items_proposed=metric.items_proposed,
        items_approved=metric.items_approved_counted,
        cycles_counted=metric.cycles_counted,
        limits=CadenceLimits(min_proposals=1, cost_threshold_usd=50.0),
    )
    assert decision.slowed


def test_metric_keys_exist_in_registry() -> None:
    assert KEY in PROGRAMS
    assert OTHER in PROGRAMS
