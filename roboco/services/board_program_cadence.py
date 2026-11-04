"""Per-program cost-per-cycle vs proposal approval rate (read-only metric)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import String, cast, func, select

from roboco.db.tables import AgentSpawnSessionTable, BoardProgramCycleTable
from roboco.foundation.policy.board_programs import PROGRAMS
from roboco.models.board_program_cadence import ProgramCadenceMetric
from roboco.services.base import BaseService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


class BoardProgramCadenceService(BaseService):
    """Joins the cycle ledger to the exploration task's spawn-session spend."""

    service_name = "board_program_cadence"

    async def program_cadence_metrics(self) -> dict[str, ProgramCadenceMetric]:
        """A ``ProgramCadenceMetric`` for every key in ``PROGRAMS``.

        Stable interface for the cadence policy: ``program_key``,
        ``cycles_counted``, ``cycles_missing_cost``, ``total_cost_usd``,
        ``cost_per_cycle_usd`` (``total_cost_usd / cycles_counted``),
        ``items_proposed``, ``items_approved`` and ``approval_rate``
        (``items_approved / items_proposed``). A program with no cycles gets
        zeros and None rates. Cost is the exploration task's summed
        ``agent_spawn_sessions.estimated_cost_usd`` (the source of
        ``TaskService.task_spend_usd``); a cycle whose ``exploration_task_id``
        is null is counted in ``cycles_missing_cost`` and left out of the cost
        mean. Proposals and approvals cover every cycle of the program.
        """
        cycles = await self._cycle_aggregates()
        costs = await self._cost_by_program()
        out: dict[str, ProgramCadenceMetric] = {}
        for key in PROGRAMS:
            counted, missing, proposed, approved = cycles.get(key, (0, 0, 0, 0))
            total = costs.get(key, 0.0)
            out[key] = ProgramCadenceMetric(
                program_key=key,
                cycles_counted=counted,
                cycles_missing_cost=missing,
                total_cost_usd=total,
                cost_per_cycle_usd=total / counted if counted else None,
                items_proposed=proposed,
                items_approved=approved,
                approval_rate=approved / proposed if proposed else None,
            )
        return out

    async def _cycle_aggregates(self) -> dict[str, tuple[int, int, int, int]]:
        """``program_key -> (counted, missing_cost, proposed, approved)``."""
        has_task = BoardProgramCycleTable.exploration_task_id.is_not(None)
        rows = await self.session.execute(
            select(
                BoardProgramCycleTable.program_key,
                func.count().filter(has_task),
                func.count().filter(~has_task),
                func.coalesce(func.sum(BoardProgramCycleTable.items_proposed), 0),
                func.coalesce(func.sum(BoardProgramCycleTable.items_approved), 0),
            ).group_by(BoardProgramCycleTable.program_key)
        )
        return {
            key: (int(counted), int(missing), int(proposed), int(approved))
            for key, counted, missing, proposed, approved in rows.all()
        }

    async def _cost_by_program(self) -> dict[str, float]:
        """``program_key -> summed spawn-session spend`` of its cycles' tasks."""
        rows = await self.session.execute(
            select(
                BoardProgramCycleTable.program_key,
                func.coalesce(func.sum(AgentSpawnSessionTable.estimated_cost_usd), 0.0),
            )
            .join(
                AgentSpawnSessionTable,
                AgentSpawnSessionTable.task_id
                == cast(BoardProgramCycleTable.exploration_task_id, String),
            )
            .group_by(BoardProgramCycleTable.program_key)
        )
        return {key: float(total) for key, total in rows.all()}


def get_board_program_cadence_service(
    session: AsyncSession,
) -> BoardProgramCadenceService:
    """Construct a BoardProgramCadenceService bound to ``session``."""
    return BoardProgramCadenceService(session)
