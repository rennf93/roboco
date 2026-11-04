"""CEO cadence review + effective interval over patched metrics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from roboco.config import settings as cfg
from roboco.foundation.policy.board_programs import PROGRAMS
from roboco.models.board_program_cadence import ProgramCadenceMetric
from roboco.services.board_program_cadence import BoardProgramCadenceService
from roboco.services.board_program_cadence_policy import (
    base_interval_seconds,
    get_board_program_cadence_policy_service,
)
from roboco.services.board_programs import BoardProgramEngine

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SLOW = "scales"
FAST = "roadmap"
SAMPLE = 6
BASE = 1000
OVERRIDE = 12345


def _metric(
    key: str, *, cost: float | None, proposed: int, approved: int
) -> ProgramCadenceMetric:
    return ProgramCadenceMetric(
        program_key=key,
        cycles_counted=1 if cost is not None else 0,
        cycles_missing_cost=0,
        total_cost_usd=cost or 0.0,
        cost_per_cycle_usd=cost,
        items_proposed=proposed,
        items_approved=approved,
        approval_rate=approved / proposed if proposed else None,
        items_approved_counted=approved,
    )


@pytest.fixture(autouse=True)
def _patched_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(
        _self: BoardProgramCadenceService,
    ) -> dict[str, ProgramCadenceMetric]:
        out = {k: _metric(k, cost=None, proposed=0, approved=0) for k in PROGRAMS}
        out[SLOW] = _metric(SLOW, cost=54.86, proposed=SAMPLE, approved=0)
        out[FAST] = _metric(FAST, cost=10.0, proposed=SAMPLE, approved=SAMPLE)
        return out

    monkeypatch.setattr(BoardProgramCadenceService, "program_cadence_metrics", fake)


@pytest.mark.asyncio
async def test_review_lists_every_program_with_slowdown_numbers(
    db_session: AsyncSession,
) -> None:
    rows = {
        r.program_key: r
        for r in await get_board_program_cadence_policy_service(db_session).review()
    }
    assert set(rows) == set(PROGRAMS)
    slow = rows[SLOW]
    assert slow.slowed
    assert slow.cost_per_cycle_usd == pytest.approx(54.86)
    assert slow.sample_size == SAMPLE
    assert slow.cost_threshold_usd == cfg.board_cadence_cost_threshold_usd
    assert slow.effective_interval_seconds > slow.original_interval_seconds
    assert not rows[FAST].slowed
    assert rows[FAST].effective_interval_seconds == rows[FAST].original_interval_seconds


@pytest.mark.asyncio
async def test_effective_interval_only_stretches(db_session: AsyncSession) -> None:
    svc = get_board_program_cadence_policy_service(db_session)
    assert await svc.effective_interval(SLOW, BASE) == int(
        BASE * cfg.board_cadence_slowdown_multiplier
    )
    assert await svc.effective_interval(FAST, BASE) == BASE


@pytest.mark.asyncio
async def test_ceo_can_reverse_a_slowdown(db_session: AsyncSession) -> None:
    svc = get_board_program_cadence_policy_service(db_session)
    await svc.set_slowdown_disabled(SLOW, True)
    assert await svc.effective_interval(SLOW, BASE) == BASE
    row = next(r for r in await svc.review() if r.program_key == SLOW)
    assert not row.slowed
    assert row.slowdown_disabled


@pytest.mark.asyncio
async def test_review_uses_the_scheduler_base_for_override_programs(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg, "roadmap_interval_seconds", OVERRIDE)
    assert base_interval_seconds(FAST) == OVERRIDE
    assert base_interval_seconds(SLOW) == PROGRAMS[SLOW].default_interval_seconds
    row = next(
        r
        for r in await get_board_program_cadence_policy_service(db_session).review()
        if r.program_key == FAST
    )
    assert row.original_interval_seconds == OVERRIDE
    assert row.effective_interval_seconds == OVERRIDE


async def _true(*_args: object) -> bool:
    return True


async def _false(*_args: object) -> bool:
    return False


@pytest.mark.asyncio
async def test_run_due_one_delays_a_slowed_program(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    base = PROGRAMS[SLOW].default_interval_seconds
    now = datetime.now(UTC)
    opened: list[str] = []
    state = {"last": now - timedelta(seconds=base + 1)}

    async def _state(_self: BoardProgramEngine, _key: str) -> tuple[bool, datetime]:
        return False, state["last"]

    async def _originate(_self: BoardProgramEngine, key: str) -> object:
        opened.append(key)
        return object()

    monkeypatch.setattr(BoardProgramEngine, "enabled", _true)
    monkeypatch.setattr(BoardProgramEngine, "_scope_gate", _true)
    monkeypatch.setattr(BoardProgramEngine, "_decisions_due_early", _false)
    monkeypatch.setattr(BoardProgramEngine, "_dedup_state", _state)
    monkeypatch.setattr(BoardProgramEngine, "_originate_and_record", _originate)
    engine = BoardProgramEngine(db_session)

    # Past the base interval but inside the stretched one: delayed.
    assert not await engine._run_due_one(SLOW, now)
    assert opened == []
    # Past the stretched interval: due.
    stretched = int(base * cfg.board_cadence_slowdown_multiplier)
    state["last"] = now - timedelta(seconds=stretched + 1)
    assert await engine._run_due_one(SLOW, now)
    assert opened == [SLOW]
