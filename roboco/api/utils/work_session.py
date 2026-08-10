"""
WorkSession Route Helpers

Route-glue helpers backing roboco/api/routes/work_session.py.
"""

from uuid import UUID

from fastapi import HTTPException, status

from roboco.models import AgentRole
from roboco.models.permissions import AgentContext
from roboco.services.work_session import WorkSessionService

# =============================================================================
# OWNERSHIP GUARD
#
# Every mutating route keys off session_id alone, so without a re-check any
# developer could mutate a peer's session and any PM could merge any cell's PR
# — bypassing the verb layer's active-claimant gate. Re-assert the caller owns
# the session (dev ops) or owns the session's task cell (PM ops) before the
# service call (#158).
# =============================================================================


async def _assert_cell_pm_owns_session(
    service: WorkSessionService, session_id: UUID, agent: AgentContext
) -> None:
    """Raise 403 unless ``agent`` is a cell PM who owns the session's task cell."""
    if agent.role != AgentRole.CELL_PM:
        return
    team = await service.task_team_for_session(session_id)
    if agent.team is None or team is None or team != agent.team:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="cell PM does not own this session's task cell",
        )


async def _assert_ownership(
    service: WorkSessionService,
    session_id: UUID,
    agent: AgentContext,
    *,
    pm_op: bool,
) -> None:
    """Fetch the session and verify the caller may mutate it.

    Raises 404 for a missing session, 403 for a wrong-owner / wrong-cell caller.
    Dev ops require the caller to BE the session's agent. PM ops (merge_pr)
    require a cell PM to own the session's task cell; main PM / CEO / board
    coordinate every cell and are admitted by the role gate alone.
    """
    session = await service.get(session_id)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Work session not found: {session_id}",
        )
    if pm_op:
        await _assert_cell_pm_owns_session(service, session_id, agent)
        return
    if session.agent_id != agent.agent_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="not the owner of this work session",
        )
