"""Dev dispatch honours the work author's single-active-task rule.

Live 2026-10-28, 05:56-06:57: be-dev-1 was spawned 14 times on tasks its own
``i_will_work_on`` refused ("You have 2 paused task(s); resume before claiming
new work") and never once on those paused tasks, while the router kept
claiming new work for developers that still held some. The dispatcher now
asks the same rule (``TaskService.held_task_blocking_claim``) before it spawns
or routes a developer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.seeds.initial_data import AGENT_UUIDS

if TYPE_CHECKING:
    import httpx

_BE_DEV_1 = AGENT_UUIDS["be-dev-1"]
_BE_DEV_2 = AGENT_UUIDS["be-dev-2"]


def _orch() -> AgentOrchestrator:
    orch = object.__new__(AgentOrchestrator)
    orch._instances = {}
    return orch


def _dev_task(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": str(uuid4()),
        "status": "pending",
        "team": "backend",
        "task_type": "code",
        "title": "Code leaf",
        "assigned_to": _BE_DEV_1,
    }
    base.update(over)
    return base


_CLIENT = cast("httpx.AsyncClient", object())


async def _dispatch(
    orch: AgentOrchestrator, task: dict[str, Any], held: tuple[str, str] | None
) -> dict[str, AsyncMock]:
    mocks = {
        "probe": AsyncMock(return_value=held),
        "resume": AsyncMock(),
        "release": AsyncMock(),
        "spawn_pending": AsyncMock(),
        "existing_owner": AsyncMock(),
    }
    with (
        patch.object(orch, "_is_agent_active", return_value=False),
        patch.object(orch, "_held_task_blocking_claim", new=mocks["probe"]),
        patch.object(orch, "_resume_paused_task_for_author", new=mocks["resume"]),
        patch.object(orch, "_release_claim_to_pending", new=mocks["release"]),
        patch.object(orch, "_spawn_pending_dev", new=mocks["spawn_pending"]),
        patch.object(orch, "_handle_dev_existing_owner", new=mocks["existing_owner"]),
    ):
        await orch._dev_dispatch_one(_CLIENT, task)
    return mocks


@pytest.mark.asyncio
async def test_idle_dev_holding_a_paused_task_is_resumed_there() -> None:
    paused_id = str(uuid4())
    mocks = await _dispatch(_orch(), _dev_task(), ("paused", paused_id))

    mocks["resume"].assert_awaited_once_with(_CLIENT, paused_id, "be-dev-1")
    mocks["spawn_pending"].assert_not_awaited()


@pytest.mark.asyncio
async def test_dev_holding_active_work_is_left_to_finish_it() -> None:
    mocks = await _dispatch(
        _orch(), _dev_task(status="needs_revision"), ("in_progress", str(uuid4()))
    )

    for name in ("resume", "release", "spawn_pending", "existing_owner"):
        mocks[name].assert_not_awaited()


@pytest.mark.asyncio
async def test_a_second_unstarted_claim_is_released_to_the_pool() -> None:
    task = _dev_task(status="claimed", claimed_by=_BE_DEV_1)
    mocks = await _dispatch(_orch(), task, ("claimed", str(uuid4())))

    mocks["release"].assert_awaited_once_with(task["id"])
    mocks["existing_owner"].assert_not_awaited()


@pytest.mark.asyncio
async def test_a_free_dev_is_dispatched_as_before() -> None:
    task = _dev_task()
    mocks = await _dispatch(_orch(), task, None)

    mocks["probe"].assert_awaited_once_with(_BE_DEV_1, task["id"])
    mocks["spawn_pending"].assert_awaited_once_with(_CLIENT, task, "be-dev-1")


@pytest.mark.asyncio
async def test_an_in_progress_task_needs_no_claim_so_skips_the_probe() -> None:
    task = _dev_task(status="in_progress")
    mocks = await _dispatch(_orch(), task, ("paused", str(uuid4())))

    mocks["probe"].assert_not_awaited()
    mocks["existing_owner"].assert_awaited_once_with(task, "in_progress", "be-dev-1")


# ---------------------------------------------------------------------------
# _resume_paused_task_for_author
# ---------------------------------------------------------------------------


def _client(status_code: int = 200) -> MagicMock:
    client = MagicMock()
    client.patch = AsyncMock(return_value=MagicMock(status_code=status_code))
    return client


async def _resume(
    orch: AgentOrchestrator,
    client: MagicMock,
    task: dict[str, Any] | None,
    *,
    breaker: bool = False,
) -> AsyncMock:
    spawn = AsyncMock()
    with (
        patch.object(orch, "_fetch_task_fields", new=AsyncMock(return_value=task)),
        patch.object(
            orch, "_pm_respawn_should_gate", new=AsyncMock(return_value=breaker)
        ),
        patch.object(orch, "_build_dev_prompt", new=AsyncMock(return_value="p")),
        patch.object(orch, "_task_git_context", return_value=None),
        patch.object(type(orch), "_api_url", new=property(lambda _self: "http://api")),
        patch.object(orch, "spawn_agent", new=spawn),
    ):
        await orch._resume_paused_task_for_author(
            cast("httpx.AsyncClient", client), "t-1", "be-dev-1"
        )
    return spawn


@pytest.mark.asyncio
async def test_resume_moves_the_task_to_in_progress_then_spawns_its_author() -> None:
    client = _client()
    spawn = await _resume(_orch(), client, {"id": "t-1", "status": "paused"})

    client.patch.assert_awaited_once_with(
        "http://api/tasks/t-1", json={"status": "in_progress"}
    )
    spawn.assert_awaited_once()
    assert spawn.await_args is not None
    assert spawn.await_args.kwargs["agent_id"] == "be-dev-1"
    assert spawn.await_args.kwargs["task_id"] == "t-1"
    assert spawn.await_args.kwargs["spawned_by"] == "_resume_paused_task_for_author"


@pytest.mark.asyncio
async def test_resume_respects_the_respawn_breaker() -> None:
    client = _client()
    spawn = await _resume(
        _orch(), client, {"id": "t-1", "status": "paused"}, breaker=True
    )

    client.patch.assert_not_awaited()
    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_refused_resume_spawns_nothing() -> None:
    spawn = await _resume(_orch(), _client(409), {"id": "t-1", "status": "paused"})

    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_task_no_longer_paused_is_left_alone() -> None:
    client = _client()
    spawn = await _resume(_orch(), client, {"id": "t-1", "status": "in_progress"})

    client.patch.assert_not_awaited()
    spawn.assert_not_awaited()


# ---------------------------------------------------------------------------
# _free_cell_dev / dev routing
# ---------------------------------------------------------------------------


async def _free_dev(
    held_by: dict[str, tuple[str, str] | None], active: frozenset[str] = frozenset()
) -> tuple[str | None, AsyncMock]:
    orch = _orch()
    probe = AsyncMock(side_effect=lambda agent_uuid, _task_id: held_by[agent_uuid])
    with (
        patch.object(orch, "_is_agent_active", side_effect=lambda s: s in active),
        patch.object(orch, "_is_claim_in_flight", return_value=False),
        patch.object(orch, "_held_task_blocking_claim", new=probe),
    ):
        picked = await orch._free_cell_dev({"id": "t-1", "team": "backend"})
    return picked, probe


@pytest.mark.asyncio
async def test_routing_skips_a_dev_still_holding_work() -> None:
    picked, _probe = await _free_dev({_BE_DEV_1: ("paused", "p-1"), _BE_DEV_2: None})

    assert picked == "be-dev-2"


@pytest.mark.asyncio
async def test_routing_defers_when_every_idle_dev_holds_work() -> None:
    picked, _probe = await _free_dev(
        {_BE_DEV_1: ("paused", "p-1"), _BE_DEV_2: ("blocked", "b-1")}
    )

    assert picked is None


@pytest.mark.asyncio
async def test_routing_never_probes_a_running_dev() -> None:
    picked, probe = await _free_dev(
        {_BE_DEV_1: None, _BE_DEV_2: None}, active=frozenset({"be-dev-1"})
    )

    assert picked == "be-dev-2"
    probe.assert_awaited_once_with(_BE_DEV_2, "t-1")


@pytest.mark.asyncio
async def test_dev_routing_resolves_through_the_free_dev_check() -> None:
    orch = _orch()
    task = {"id": "t-1", "team": "backend", "task_type": "code"}
    with (
        patch.object(orch, "_resolve_routing_target", return_value="be-dev-1"),
        patch.object(
            orch, "_free_cell_dev", new=AsyncMock(return_value="be-dev-2")
        ) as free,
    ):
        assert await orch._get_routing_target("dev", task) == "be-dev-2"
    free.assert_awaited_once_with(task)
