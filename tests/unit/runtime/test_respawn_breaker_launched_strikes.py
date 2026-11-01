"""The respawn breaker strikes only spawns that really started a container.

Live 2026-10-28 05:20-05:22: the hummin provider was parked. be-qa on 106f46e3
(awaiting_qa) went through the breaker three times, each attempt bailing in
``spawn_agent`` with "Spawn skipped: provider rate-limited (parked)" at
05:20:41, 05:20:52 and 05:21:25, and the fourth consult (05:21:56) logged
"PM respawn loop detected - skipping spawn" with spawn_attempts 4, marked the
task stalled and paged the CEO, for an agent that had never run once. 19 trips
and 19 stuck-agent notifications fired in those two minutes, most for pairs
never actually spawned. The strike was taken at the gate, before
``spawn_agent`` ever decided whether to launch.
"""

from __future__ import annotations

import asyncio
import contextlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from roboco.models.runtime import AgentInstance
from roboco.runtime.orchestrator import AgentOrchestrator, AgentState
from roboco.seeds.initial_data import AGENT_UUIDS

_TASK_ID = "106f46e3-ad66-4a32-a546-3ba53c6ad0f3"
_LOGGED_PARKED_TICKS = 3


def _orchestrator(monkeypatch: pytest.MonkeyPatch) -> AgentOrchestrator:
    """A dispatcher whose spawn path runs for real down to the container
    launch; DB, audit and notification side effects are observed mocks."""
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._instances = {}
    orch._lock = asyncio.Lock()
    orch._tick_handled_tasks = set()
    orch._bg_tasks = set()
    orch._running = True
    orch._pm_respawn_tracker = {}
    monkeypatch.setattr(orch, "_schedule_respawn_persist", lambda *_a, **_k: None)
    monkeypatch.setattr(orch, "_review_claimant_slug", AsyncMock(return_value=None))
    monkeypatch.setattr(orch, "_pm_respawn_spawn_anyway", AsyncMock(return_value=False))
    monkeypatch.setattr(orch, "_mark_task_stalled", AsyncMock())
    monkeypatch.setattr(orch, "_notify_stuck_agent", AsyncMock())
    monkeypatch.setattr(orch, "_coroner_wedged_postmortem", AsyncMock())
    monkeypatch.setattr(orch, "_readiness_gate", AsyncMock(return_value=None))
    monkeypatch.setattr(
        orch, "_resolve_spawn_git_context", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(orch, "_task_git_context", lambda _task: None)
    monkeypatch.setattr(orch, "_build_qa_prompt", lambda _task: "qa prompt")
    route = SimpleNamespace(
        provider_type=SimpleNamespace(value="hummin"),
        model_name="m",
        base_url=None,
        auth_token=None,
    )
    monkeypatch.setattr(orch, "_resolve_agent_route", AsyncMock(return_value=route))
    return orch


def _wire_real_launch(orch: AgentOrchestrator, monkeypatch: pytest.MonkeyPatch) -> None:
    """Let ``_launch_spawn`` run for real on a stubbed container start."""

    async def _prepare(
        agent_id: str, task_id: str | None, *_a: Any
    ) -> tuple[Any, AgentInstance, None]:
        config = SimpleNamespace(agent_id=agent_id, provider_type="hummin", model="m")
        instance = AgentInstance(
            agent_id=agent_id, state=AgentState.STARTING, current_task_id=task_id
        )
        orch._instances[agent_id] = instance
        return config, instance, None

    monkeypatch.setattr(orch, "_prepare_agent_spawn", _prepare)
    monkeypatch.setattr(orch, "_spawn_container", AsyncMock(return_value="c" * 64))
    monkeypatch.setattr(orch, "_fire_audit", MagicMock())
    monkeypatch.setattr(orch, "_record_spawn_session", AsyncMock(return_value=None))


@contextlib.contextmanager
def _quiet_audit() -> Any:
    audit = AsyncMock()
    audit.has_recent_tracing_gap = AsyncMock(return_value=False)
    with patch("roboco.services.audit.get_audit_service", return_value=audit):
        yield


def _awaiting_qa_task() -> dict[str, Any]:
    return {
        "id": _TASK_ID,
        "status": "awaiting_qa",
        "team": "backend",
        "assigned_to": AGENT_UUIDS["be-qa"],
    }


async def _dispatch_tick(orch: AgentOrchestrator, task: dict[str, Any]) -> None:
    """One dispatcher tick of the assigned-QA path, as the live run took it."""
    orch._tick_handled_tasks = set()
    await orch._spawn_assigned_qa(task, task["assigned_to"])


@pytest.mark.asyncio
async def test_parked_spawns_never_trip_the_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The logged sequence: three parked bails, then a fourth consult. None of
    them launched a container, so the fourth must not trip or page the CEO."""
    orch = _orchestrator(monkeypatch)
    parked = AsyncMock(return_value=True)
    monkeypatch.setattr(orch, "_provider_spawn_parked", parked)
    task = _awaiting_qa_task()

    with _quiet_audit():
        for _ in range(_LOGGED_PARKED_TICKS + 1):
            await _dispatch_tick(orch, task)

    assert parked.await_count == _LOGGED_PARKED_TICKS + 1, (
        "every tick must reach spawn_agent's parked check, not the breaker"
    )
    orch_any: Any = orch
    orch_any._notify_stuck_agent.assert_not_awaited()
    orch_any._mark_task_stalled.assert_not_awaited()
    assert orch._pm_respawn_tracker[("be-qa", _TASK_ID)]["count"] == 1


@pytest.mark.asyncio
async def test_launched_unproductive_spawns_still_trip_at_the_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Spawns that really started and exited without advancing the task keep
    the breaker's threshold exactly: three launches, the fourth trips."""
    orch = _orchestrator(monkeypatch)
    monkeypatch.setattr(orch, "_provider_spawn_parked", AsyncMock(return_value=False))
    _wire_real_launch(orch, monkeypatch)
    task = _awaiting_qa_task()
    launches = 0

    with _quiet_audit():
        for _ in range(orch._PM_RESPAWN_MAX_UNPRODUCTIVE + 1):
            await _dispatch_tick(orch, task)
            instance = orch._instances.get("be-qa")
            if instance is not None and instance.state is AgentState.ACTIVE:
                launches += 1
                # The agent exits without advancing the task.
                instance.state = AgentState.OFFLINE

    assert launches == orch._PM_RESPAWN_MAX_UNPRODUCTIVE
    orch_any: Any = orch
    orch_any._notify_stuck_agent.assert_awaited_once()


@pytest.mark.asyncio
async def test_parked_attempts_between_launches_do_not_add_strikes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A park between two real launches keeps the count of real launches."""
    orch = _orchestrator(monkeypatch)
    provider = {"parked": False}

    async def _parked(*_a: Any, **_k: Any) -> bool:
        return provider["parked"]

    monkeypatch.setattr(orch, "_provider_spawn_parked", _parked)
    _wire_real_launch(orch, monkeypatch)
    task = _awaiting_qa_task()

    with _quiet_audit():
        for parked_this_tick in (False, True, True, False):
            provider["parked"] = parked_this_tick
            await _dispatch_tick(orch, task)
            instance = orch._instances.get("be-qa")
            if instance is not None:
                instance.state = AgentState.OFFLINE

    real_launches = 2
    assert orch._pm_respawn_tracker[("be-qa", _TASK_ID)]["count"] == real_launches


@pytest.mark.asyncio
async def test_restored_record_without_launch_flag_keeps_its_strikes() -> None:
    """A counter restored from the database carries no launch flag; it counts
    as launched, so a restored count one short of the threshold still trips
    on its next attempt (durability across a restart is unchanged)."""
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._bg_tasks = set()
    orch._pm_respawn_tracker = {
        ("be-pm", _TASK_ID): {
            "count": AgentOrchestrator._PM_RESPAWN_MAX_UNPRODUCTIVE,
            "last_status": "pending",
            "last_check": None,
            "tracing_resets": 0,
            "notified": False,
        }
    }
    orch_any: Any = orch
    orch_any._schedule_respawn_persist = lambda *_a, **_k: None
    orch_any._pm_respawn_spawn_anyway = AsyncMock(return_value=False)
    orch_any._pm_trip_stall_notice = AsyncMock()
    orch_any._pm_made_rule_following_retry = AsyncMock(return_value=False)

    gated = await orch._pm_respawn_should_gate(
        "be-pm", {"id": _TASK_ID, "status": "pending"}
    )

    assert gated is True
