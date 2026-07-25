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

from roboco.api.deps import CurrentAgentContext, DbSession, require_ceo_role
from roboco.foundation.policy.board_programs import PROGRAMS
from roboco.security import guard_deco
from roboco.services.board_programs import BoardProgramEngine, get_board_program_engine

router = APIRouter()


def _require_ceo(agent: CurrentAgentContext) -> None:
    require_ceo_role(agent.role, action="view or act on Board Programs")


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


async def _to_response(engine: BoardProgramEngine, key: str) -> BoardProgramResponse:
    program = PROGRAMS[key]
    enabled = await engine.enabled(key)
    open_cycle, last_opened_at = await engine.cycle_state(key)
    summary = await engine.prior_cycle_context(key, limit=1)
    opted_in = await engine.opted_in_projects(program)
    return BoardProgramResponse(
        key=key,
        title=program.title or key,
        description=program.description,
        role=program.role,
        trigger=program.trigger.value,
        scope=program.scope,
        enabled=enabled,
        opted_in_project_slugs=[p.slug for p in opted_in],
        last_opened_at=last_opened_at.isoformat() if last_opened_at else None,
        open_cycle=open_cycle,
        last_cycle_summary=summary or None,
    )


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
