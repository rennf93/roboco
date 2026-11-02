"""While the dispatch maintenance pause is on, spawn_agent spawns nothing.

Live 2026-11-02: with dispatch, board programs and engines all paused, the
dispatcher spawned 14 agents in 15 minutes through its respawn paths (an
assigned PM, an orphaned task, an agentless claim), none of which asked the
pause. The spawn chokepoint now refuses every one of them.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator, AgentReadinessError
from roboco.services.maintenance_pause import PauseScope


def _orchestrator(*, dispatch_paused: bool) -> tuple[AgentOrchestrator, AsyncMock]:
    orch = object.__new__(AgentOrchestrator)
    paused = AsyncMock(return_value=dispatch_paused)
    object.__setattr__(orch, "_is_paused", paused)
    object.__setattr__(
        orch, "_readiness_gate", AsyncMock(return_value="stubbed-not-ready")
    )
    return orch, paused


@pytest.mark.asyncio
async def test_a_respawn_is_refused_while_dispatch_is_paused() -> None:
    orch, paused = _orchestrator(dispatch_paused=True)

    with pytest.raises(AgentReadinessError, match="dispatch is paused"):
        await orch.spawn_agent(
            "be-dev-1",
            task_id="f5d66db6",
            spawned_by="_respawn_agentless_claim_assignee",
        )

    paused.assert_awaited_once_with(PauseScope.DISPATCH)


@pytest.mark.asyncio
async def test_without_the_pause_the_spawn_reaches_the_readiness_gate() -> None:
    orch, _paused = _orchestrator(dispatch_paused=False)

    with pytest.raises(AgentReadinessError, match="stubbed-not-ready"):
        await orch.spawn_agent("be-dev-1", task_id="f5d66db6")
