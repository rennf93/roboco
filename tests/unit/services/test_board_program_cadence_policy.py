"""CEO cadence review + effective interval over patched metrics."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from roboco.config import settings as cfg
from roboco.foundation.policy.board_programs import PROGRAMS
from roboco.models.board_program_cadence import ProgramCadenceMetric
from roboco.services.board_program_cadence import BoardProgramCadenceService
from roboco.services.board_program_cadence_policy import (
    get_board_program_cadence_policy_service,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

SLOW = "scales"
FAST = "roadmap"
SAMPLE = 6
BASE = 1000


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
    assert await svc.effective_interval(SLOW, BASE) >= BASE
    assert await svc.effective_interval(FAST, BASE) == BASE


@pytest.mark.asyncio
async def test_ceo_can_reverse_a_slowdown(db_session: AsyncSession) -> None:
    svc = get_board_program_cadence_policy_service(db_session)
    await svc.set_slowdown_disabled(SLOW, True)
    assert await svc.effective_interval(SLOW, BASE) == BASE
    row = next(r for r in await svc.review() if r.program_key == SLOW)
    assert not row.slowed
    assert row.slowdown_disabled
