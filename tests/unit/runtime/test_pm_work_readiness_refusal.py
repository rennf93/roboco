"""A spawn refused as not ready is one task's outcome, not the end of pm_work.

Live 2026-10-28 06:40:08: ``_dispatch_pm_work`` reached d42e53ea, a pending task
assigned to fe-pm whose dependency dd60608d was still open. ``spawn_agent``'s
readiness gate auto-blocked it ("Auto-blocked task with missing prerequisites")
and raised ``AgentReadinessError``, whose documented contract is that callers
log and move on. Nothing in pm_work caught it: the tick loop logged
"Dispatcher raised; continuing with next dispatcher" (dispatcher pm_work) and
every pending task after d42e53ea went undispatched that tick.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator, AgentReadinessError
from roboco.seeds.initial_data import AGENT_UUIDS

_NOT_READY = "d42e53ea-a031-4802-bb05-b4c43ea55ca4"
_NEXT = "0c4e2592-be20-43f3-976d-afb4c201e4b1"
_LOGGED_REFUSAL = (
    f"spawn refused for fe-pm (task={_NOT_READY}): Task {_NOT_READY} waiting on"
    " non-terminal dependency dd60608d-d8dc-4c6b-b4f0-c6ee9a200839"
)


def _pending(task_id: str, slug: str) -> dict[str, Any]:
    return {
        "id": task_id,
        "status": "pending",
        "team": "backend",
        "source": "manual",
        "assigned_to": AGENT_UUIDS[slug],
    }


def _orchestrator(
    monkeypatch: pytest.MonkeyPatch, tasks: list[dict[str, Any]]
) -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._instances = {}
    orch._tick_handled_tasks = set()
    monkeypatch.setattr(orch, "_fetch_tasks", AsyncMock(return_value=tasks))
    monkeypatch.setattr(orch, "_is_paused", AsyncMock(return_value=False))
    monkeypatch.setattr(orch, "_pm_respawn_should_gate", AsyncMock(return_value=False))
    monkeypatch.setattr(orch, "_build_pm_triage_prompt", lambda _task: "triage")
    monkeypatch.setattr(orch, "_task_git_context", lambda _task: None)

    async def _spawn(*, task_id: str, **_kw: Any) -> None:
        if task_id == _NOT_READY:
            raise AgentReadinessError(_LOGGED_REFUSAL)

    monkeypatch.setattr(orch, "spawn_agent", AsyncMock(side_effect=_spawn))
    return orch


@pytest.mark.asyncio
async def test_readiness_refusal_does_not_stop_the_rest_of_the_pm_tick(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _orchestrator(
        monkeypatch, [_pending(_NOT_READY, "fe-pm"), _pending(_NEXT, "be-pm")]
    )

    await orch._dispatch_pm_work(client=MagicMock())

    orch_any: Any = orch
    spawned = [c.kwargs["task_id"] for c in orch_any.spawn_agent.await_args_list]
    assert spawned == [_NOT_READY, _NEXT]


@pytest.mark.asyncio
async def test_unexpected_errors_still_reach_the_tick_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the expected refusal is absorbed; a real fault still surfaces to
    ``_dispatch_all_work``'s per-dispatcher guard and its error log."""
    orch = _orchestrator(monkeypatch, [_pending(_NEXT, "be-pm")])
    monkeypatch.setattr(
        orch, "spawn_agent", AsyncMock(side_effect=RuntimeError("docker gone"))
    )

    with pytest.raises(RuntimeError, match="docker gone"):
        await orch._dispatch_pm_work(client=MagicMock())
