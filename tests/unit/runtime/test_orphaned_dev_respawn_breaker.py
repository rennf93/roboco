"""The dev dispatcher's orphaned-task respawn is held to the respawn breaker.

``_dev_dispatch_one`` respawns the owner of a claimed / in_progress /
needs_revision dev task whose container is gone through
``_respawn_dev_if_inactive`` ("Respawning agent for orphaned task"), the one
task-keyed spawn path that never consulted ``_pm_respawn_should_gate``. Live
2026-10-28 fe-dev-1 was respawned on in_progress 86ed0eab at 06:01:04 and again
at 06:03:12 with the task still in_progress; a dev that keeps exiting without
advancing its task was respawned every tick for as long as that lasted.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.seeds.initial_data import AGENT_UUIDS

_TASK_ID = "86ed0eab-b684-449b-94bb-b4fbc7a4461b"
_TICKS = 5


def _orchestrator(monkeypatch: pytest.MonkeyPatch) -> AgentOrchestrator:
    """A dev dispatcher whose breaker runs for real; each spawn launches."""
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._instances = {}
    orch._tick_handled_tasks = set()
    orch._pm_respawn_tracker = {}
    orch._bg_tasks = set()
    monkeypatch.setattr(orch, "_schedule_respawn_persist", lambda *_a, **_k: None)
    monkeypatch.setattr(
        orch, "_pm_made_rule_following_retry", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(orch, "_pm_respawn_spawn_anyway", AsyncMock(return_value=False))
    monkeypatch.setattr(orch, "_pm_trip_stall_notice", AsyncMock())
    monkeypatch.setattr(orch, "_build_dev_prompt", AsyncMock(return_value="dev"))
    monkeypatch.setattr(orch, "_task_git_context", lambda _task: None)

    async def _spawn(*, agent_id: str, task_id: str, **_kw: Any) -> None:
        orch._note_respawn_spawn_launched(agent_id, task_id)

    monkeypatch.setattr(orch, "spawn_agent", AsyncMock(side_effect=_spawn))
    return orch


@pytest.mark.asyncio
async def test_orphaned_in_progress_respawns_stop_at_the_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _orchestrator(monkeypatch)
    task = {
        "id": _TASK_ID,
        "status": "in_progress",
        "team": "frontend",
        "task_type": "code",
        "assigned_to": AGENT_UUIDS["fe-dev-1"],
        "claimed_by": AGENT_UUIDS["fe-dev-1"],
    }

    for _ in range(_TICKS):
        orch._tick_handled_tasks = set()
        await orch._dev_dispatch_one(MagicMock(), task)

    orch_any: Any = orch
    assert orch_any.spawn_agent.await_count == orch._PM_RESPAWN_MAX_UNPRODUCTIVE
    orch_any._pm_trip_stall_notice.assert_awaited()


@pytest.mark.asyncio
async def test_a_running_owner_is_neither_respawned_nor_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _orchestrator(monkeypatch)
    gate = AsyncMock(return_value=False)
    monkeypatch.setattr(orch, "_pm_respawn_should_gate", gate)
    monkeypatch.setattr(orch, "_is_agent_active", lambda _slug: True)

    await orch._respawn_dev_if_inactive({"id": _TASK_ID}, "fe-dev-1")

    orch_any: Any = orch
    orch_any.spawn_agent.assert_not_awaited()
    gate.assert_not_awaited()
