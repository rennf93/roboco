"""
Board Programs Route Helpers

Route-glue helpers backing roboco/api/routes/board_programs.py.
"""

from typing import TYPE_CHECKING

from roboco.api.deps import CurrentAgentContext, require_ceo_role
from roboco.foundation.policy.board_programs import PROGRAMS
from roboco.services.board_programs import BoardProgramEngine

if TYPE_CHECKING:
    from roboco.api.routes.board_programs import BoardProgramResponse


def require_ceo(agent: CurrentAgentContext) -> None:
    require_ceo_role(agent.role, action="view or act on Board Programs")


async def to_response(engine: BoardProgramEngine, key: str) -> "BoardProgramResponse":
    # Deferred import: BoardProgramResponse stays defined in the route module
    # (it is that file's response_model, untouched by this extraction) — a
    # top-level import here would be circular since the route module imports
    # this module.
    from roboco.api.routes.board_programs import BoardProgramResponse

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
