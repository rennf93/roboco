"""An agentless claim's respawn obeys the single-active-task rule and the breaker.

``_dispatch_claimed_without_agent`` respawns the assignee of a claimed or
in_progress task that has no running container. It skipped both rules every
other task-keyed spawn path applies:

- 2026-11-01 04:55:33 it spawned be-dev-1 onto claimed 106f46e3 while be-dev-1
  still held f5d66db6 (escalated to be-pm at 04:54:17, restored to be-dev-1 in
  progress at 04:56:04). In that run be-dev-1's i_will_work_on on 106f46e3 was
  refused ("You have a in_progress task (f5d66db6...)"), and i_am_done and
  i_am_blocked were refused because the task was still 'claimed'.
- 2026-10-28 it spawned main-pm on claimed be2145b0 five times (06:28:15,
  06:29:14, 06:44:36, 06:45:46, 06:53:18) without the task leaving 'claimed'.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.seeds.initial_data import AGENT_UUIDS

_HELD_TASK = "f5d66db6-48f4-4292-9ee2-ccce055528f8"
_CLAIMED_TASK = "106f46e3-ad66-4a32-a546-3ba53c6ad0f3"
_ROOT_TASK = "be2145b0-72fd-44cc-baae-beb9a42612d9"
_LOGGED_MAIN_PM_RESPAWNS = 5


def _stale() -> str:
    """An ``updated_at`` well past the claimed-without-agent grace window."""
    return (datetime.now(UTC) - timedelta(minutes=30)).isoformat()


def _orchestrator(
    monkeypatch: pytest.MonkeyPatch, held: tuple[str, str] | None
) -> AgentOrchestrator:
    """A dispatcher whose breaker runs for real; ``held`` is what
    ``TaskService.held_task_blocking_claim`` answers for the assignee."""
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._instances = {}
    orch._tick_handled_tasks = set()
    orch._pm_respawn_tracker = {}
    orch._held_hold_logged_for = {}
    orch._bg_tasks = set()
    monkeypatch.setattr(orch, "_schedule_respawn_persist", lambda *_a, **_k: None)
    monkeypatch.setattr(
        orch, "_pm_made_rule_following_retry", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(orch, "_pm_respawn_spawn_anyway", AsyncMock(return_value=False))
    monkeypatch.setattr(orch, "_pm_trip_stall_notice", AsyncMock())
    monkeypatch.setattr(orch, "_held_task_blocking_claim", AsyncMock(return_value=held))
    monkeypatch.setattr(orch, "_release_claim_to_pending", AsyncMock())
    monkeypatch.setattr(orch, "_task_git_context", lambda _task: None)
    monkeypatch.setattr(orch, "_get_prompt_for_agent", AsyncMock(return_value="p"))

    async def _spawn(*, agent_id: str, task_id: str, **_kw: Any) -> None:
        orch._note_respawn_spawn_launched(agent_id, task_id)

    monkeypatch.setattr(orch, "spawn_agent", AsyncMock(side_effect=_spawn))
    return orch


async def _tick(orch: AgentOrchestrator, tasks: list[dict[str, Any]]) -> None:
    orch._tick_handled_tasks = set()
    orch_any: Any = orch
    orch_any._fetch_tasks = AsyncMock(return_value=tasks)
    await orch._dispatch_claimed_without_agent(client=MagicMock())


def _claimed(task_id: str, slug: str) -> dict[str, Any]:
    return {
        "id": task_id,
        "status": "claimed",
        "team": "backend",
        "assigned_to": AGENT_UUIDS[slug],
        "claimed_by": AGENT_UUIDS[slug],
        "updated_at": _stale(),
    }


@pytest.mark.asyncio
async def test_developer_holding_other_work_is_not_spawned_on_its_agentless_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 2026-11-01 sequence: be-dev-1 still holds f5d66db6 (blocked,
    escalated away from it), so it must not be booted onto claimed 106f46e3."""
    orch = _orchestrator(monkeypatch, held=("blocked", _HELD_TASK))

    await _tick(orch, [_claimed(_CLAIMED_TASK, "be-dev-1")])

    orch_any: Any = orch
    orch_any.spawn_agent.assert_not_awaited()
    orch_any._held_task_blocking_claim.assert_awaited_once_with(
        AGENT_UUIDS["be-dev-1"], _CLAIMED_TASK
    )


@pytest.mark.asyncio
async def test_second_never_started_claim_is_released_not_spawned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Held work that is itself a never-started claim: the doctrine releases
    the second claim to the pool instead of spawning onto it."""
    orch = _orchestrator(monkeypatch, held=("claimed", _HELD_TASK))

    await _tick(orch, [_claimed(_CLAIMED_TASK, "be-dev-1")])

    orch_any: Any = orch
    orch_any.spawn_agent.assert_not_awaited()
    orch_any._release_claim_to_pending.assert_awaited_once_with(_CLAIMED_TASK)


@pytest.mark.asyncio
async def test_held_back_claim_does_not_spend_the_ticks_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A claim held back by the single-active-task rule spawns nothing, so the
    next agentless claim in the same tick still gets its respawn."""
    orch = _orchestrator(monkeypatch, held=None)

    async def _held(agent_uuid: str, _task_id: str) -> tuple[str, str] | None:
        if agent_uuid == AGENT_UUIDS["be-dev-1"]:
            return ("in_progress", _HELD_TASK)
        return None

    monkeypatch.setattr(orch, "_held_task_blocking_claim", _held)

    await _tick(
        orch,
        [
            _claimed(_CLAIMED_TASK, "be-dev-1"),
            _claimed(_ROOT_TASK, "be-dev-2"),
        ],
    )

    orch_any: Any = orch
    orch_any.spawn_agent.assert_awaited_once()
    assert orch_any.spawn_agent.await_args.kwargs["agent_id"] == "be-dev-2"


@pytest.mark.asyncio
async def test_coordinator_respawns_on_a_claim_that_never_advances_stop_at_the_breaker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 2026-10-28 sequence: main-pm respawned on claimed be2145b0 every
    time it exited. A coordinator holds many roots by design, so only the
    breaker applies: three launches, then the respawns stop."""
    orch = _orchestrator(monkeypatch, held=None)
    task = _claimed(_ROOT_TASK, "main-pm")

    for _ in range(_LOGGED_MAIN_PM_RESPAWNS):
        await _tick(orch, [task])

    orch_any: Any = orch
    assert orch_any.spawn_agent.await_count == orch._PM_RESPAWN_MAX_UNPRODUCTIVE
    orch_any._pm_trip_stall_notice.assert_awaited()
    orch_any._held_task_blocking_claim.assert_not_awaited()
