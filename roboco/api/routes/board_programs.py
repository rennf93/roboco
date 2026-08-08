"""Board Programs API — CEO-only registry status + off-schedule "run now".

Mirrors ``roboco/api/routes/roadmap.py``'s CEO-gating shape. Lists every
registered program (``roboco.foundation.policy.board_programs.PROGRAMS``)
with its live settings-store enablement, dedup/open-cycle state, and
opted-in projects; ``run-now`` calls ``BoardProgramEngine.open_program_cycle``
off-schedule (enabled + dedup only, no cron-due check) — the same seam the
strategy-engine idle trigger uses.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from roboco.api.deps import CurrentAgentContext, DbSession
from roboco.api.utils.board_programs import require_ceo as _require_ceo
from roboco.api.utils.board_programs import to_response as _to_response
from roboco.foundation.policy.board_programs import PROGRAMS
from roboco.security import guard_deco
from roboco.services.board_programs import get_board_program_engine

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
    — ``open_program_cycle`` collapses all three into the same None result,
    and the caller has no actionable distinction between them beyond retry.
    """
    _require_ceo(agent)
    if key not in PROGRAMS:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Unknown Board Program"
        )
    engine = get_board_program_engine(db)
    task = await engine.open_program_cycle(key)
    if task is None:
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
