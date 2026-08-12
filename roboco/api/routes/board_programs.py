"""Board Programs API — CEO-only registry status + off-schedule "run now".

Mirrors ``roboco/api/routes/roadmap.py``'s CEO-gating shape. Lists every
registered program (``roboco.foundation.policy.board_programs.PROGRAMS``)
with its live settings-store enablement, dedup/open-cycle state, and
opted-in projects; ``run-now`` calls ``BoardProgramEngine.open_program_cycle``
off-schedule (enabled + dedup only, no cron-due check) — the same seam the
strategy-engine idle trigger uses.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from roboco.api.deps import CurrentAgentContext, DbSession
from roboco.api.utils.board_programs import cycle_to_response as _cycle_to_response
from roboco.api.utils.board_programs import require_ceo as _require_ceo
from roboco.api.utils.board_programs import to_response as _to_response
from roboco.foundation.policy.board_programs import PROGRAMS
from roboco.foundation.policy.maintenance_pause import PauseScope
from roboco.security import guard_deco
from roboco.services.board_programs import get_board_program_engine
from roboco.services.maintenance_pause import is_paused

router = APIRouter()


class BoardProgramResponse(BaseModel):
    """One registry entry's live status — the panel card + edit-project
    dialog's opt-in controls both read this shape."""

    key: str
    title: str
    description: str
    role: str
    trigger: str
    scope: str
    enabled: bool
    opted_in_project_slugs: list[str]
    last_opened_at: str | None
    open_cycle: bool
    last_cycle_summary: str | None


class BoardProgramDecisionResponse(BaseModel):
    """One CEO approve/reject on a cycle. ``item_snapshot`` is the bounded
    payload ``BoardProgramEngine.record_decision`` stamps at decision time,
    present whenever the source item was resolvable, absent for a decision
    recorded before that existed."""

    item_ref: str
    verdict: str
    reason: str | None = None
    item_snapshot: dict[str, Any] | None = None


class BoardProgramCycleResponse(BaseModel):
    """One recorded cycle for a program: the durable LEARN-history read
    surface. Survives the exploration task's deletion: every decision's
    ``item_snapshot`` (when resolved) stands on its own."""

    id: str
    opened_at: str
    closed_at: str | None
    items_proposed: int
    items_approved: int
    items_rejected: int
    nothing_to_propose_reason: str | None
    decisions: list[BoardProgramDecisionResponse]


@router.get("", response_model=list[BoardProgramResponse])
async def list_board_programs(
    db: DbSession, agent: CurrentAgentContext
) -> list[BoardProgramResponse]:
    """Every registered Board Program's live status."""
    _require_ceo(agent)
    engine = get_board_program_engine(db)
    return [await _to_response(engine, key) for key in PROGRAMS]


@router.post("/{key}/run-now", response_model=BoardProgramResponse)
@guard_deco.rate_limit(requests=30, window=60)
@guard_deco.block_clouds()
async def run_program_now(
    key: str, db: DbSession, agent: CurrentAgentContext
) -> BoardProgramResponse:
    """Open a cycle for ``key`` off-schedule.

    404 for an unregistered key; 409 when the program is disabled, already
    has an open cycle, or (a project-scoped program) has no opted-in project
    -- ``open_program_cycle`` collapses those into the same None result, and
    the caller has no actionable distinction between them beyond retry. A
    ``board_programs`` maintenance pause is checked separately here and named
    explicitly in that case, since silently folding it into the same generic
    message would send the CEO looking at the wrong control.

    Unlike ``POST /video/request`` (deliberately exempt from the ``engines``
    pause scope, see ``VideoEngine.open_video_task``'s own docstring), this
    run-now rides ``open_program_cycle``, the SAME chokepoint the cron loop
    and the metric-predicate accelerator also call. Exempting the CEO's
    manual trigger from the pause here would exempt those two autonomous
    callers too, since they share this one entry point, the opposite of
    what a pause is for. Video's on-demand path is a structurally separate
    function from its own autonomous (release-triggered) trigger, so it can
    be exempted without that leak. Honoring the pause here, not aligning
    with video's exemption, is the deliberate choice.
    """
    _require_ceo(agent)
    if key not in PROGRAMS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown Board Program"
        )
    engine = get_board_program_engine(db)
    task = await engine.open_program_cycle(key)
    if task is None:
        if await is_paused(db, PauseScope.BOARD_PROGRAMS):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Could not open a cycle - the board_programs maintenance "
                    "pause is active. Resume it from the maintenance-pause "
                    "control, or wait for it to auto-expire, then try again."
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Could not open a cycle — the program may be disabled, already "
                "have an open cycle, or have no opted-in project"
            ),
        )
    # Write route commits explicitly (get_db auto-commit is unreliable).
    await db.commit()
    return await _to_response(engine, key)


@router.get("/{key}/cycles", response_model=list[BoardProgramCycleResponse])
async def list_program_cycles(
    key: str,
    db: DbSession,
    agent: CurrentAgentContext,
    limit: int = Query(20, ge=1, le=100),
) -> list[BoardProgramCycleResponse]:
    """Historical cycles for ``key``, newest-opened-first, capped by
    ``limit``: the durable read surface behind the single rendered
    ``last_cycle_summary`` line ``list_board_programs`` returns. 404 for an
    unregistered key."""
    _require_ceo(agent)
    if key not in PROGRAMS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown Board Program"
        )
    engine = get_board_program_engine(db)
    cycles = await engine.list_cycles(key, limit=limit)
    return [_cycle_to_response(c) for c in cycles]
