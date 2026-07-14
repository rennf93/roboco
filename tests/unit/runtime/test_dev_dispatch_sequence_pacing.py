"""Dev dispatch sequence pacing — no doomed spawn for a sequence-held task.

``_spawn_pending_dev`` boots a full dev container for a pre-assigned pending
task. Before this guard, a task held only by the assignee-blind sequence
guard (a non-terminal lower-sequence same-parent sibling, not a declared
dependency) passed every pre-spawn gate — ``_blocked_by_earlier_lane_sibling``
is narrower (same dev's lane) and ``_validate_task_for_spawn`` checks declared
dependencies, not sequence siblings — so the container spawned, the agent's
first claim hit ``_claim_blocked_by_sequence`` at the chokepoint and was
refused, and the agent exited only to be re-spawned next tick. Mirror the PM
path's ``_pending_claim_blocked`` prefilter so a sequence-held task is skipped
pre-spawn instead of churned.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator

if TYPE_CHECKING:
    import httpx


def _orch() -> AgentOrchestrator:
    orch = object.__new__(AgentOrchestrator)
    orch._instances = {}
    return orch


def _pending_dev_task(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": str(uuid4()),
        "status": "pending",
        "team": "backend",
        "task_type": "code",
        "title": "Sequenced code leaf",
        "assigned_to": str(uuid4()),
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_sequence_held_dev_task_not_spawned() -> None:
    """A sequence-held task skips spawn entirely — no container, no churn."""
    orch = _orch()
    task = _pending_dev_task()
    client = cast("httpx.AsyncClient", object())
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_pending_claim_blocked", new=AsyncMock(return_value=True)),
        patch.object(orch, "_blocked_by_earlier_lane_sibling", new=AsyncMock()) as lane,
        patch.object(orch, "_pm_respawn_should_gate", new=AsyncMock()) as respawn,
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._spawn_pending_dev(client, task, "be-dev-1")

    spawn.assert_not_awaited()
    # Sequence guard is broader and checked first — narrower per-dev probe never runs.
    lane.assert_not_awaited()
    respawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_ready_dev_task_still_spawns() -> None:
    """A clear task (prefilter False, lane clear, respawn gate clear, valid)
    proceeds to spawn unchanged."""
    orch = _orch()
    task = _pending_dev_task()
    client = cast("httpx.AsyncClient", object())
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_pending_claim_blocked", new=AsyncMock(return_value=False)),
        patch.object(orch, "_blocked_by_earlier_lane_sibling", new=AsyncMock(return_value=False)),
        patch.object(orch, "_pm_respawn_should_gate", new=AsyncMock(return_value=False)),
        patch.object(orch, "_validate_task_for_spawn", new=AsyncMock(return_value=None)),
        patch.object(orch, "_get_prompt_for_agent", new=AsyncMock(return_value="prompt")),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(orch, "spawn_agent", new=AsyncMock()) as spawn,
    ):
        await orch._spawn_pending_dev(client, task, "be-dev-1")

    spawn.assert_awaited_once()