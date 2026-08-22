"""Maintenance-pause gates threaded through the orchestrator's dispatch tick.

Covers the trap this drill exists to close (the stale-claim reaper reading a
paused, quiet fleet as wedged) plus the dispatch/board_programs scope split
inside ``_dispatch_pm_work`` and ``_dispatch_board_program_exploration``.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
import roboco.db.base as db_base
import roboco.services.maintenance_pause as mp_module
from roboco.foundation.policy.maintenance_pause import PauseScope
from roboco.models.runtime import AgentInstance
from roboco.runtime.orchestrator import (
    ROADMAP_SOURCE,
    AgentOrchestrator,
    AgentState,
    _dispatch_board_program_exploration,
)
from roboco.seeds.initial_data import AGENT_UUIDS


def _make_orch() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    cast("Any", orch)._pm_respawn_tracker = {}
    cast("Any", orch)._schedule_respawn_persist = lambda *_a, **_k: None
    orch._instances = {}
    orch._board_dispatched = set()
    return orch


# ---------------------------------------------------------------------------
# _is_paused helper
# ---------------------------------------------------------------------------


class _FakeAsyncSession:
    async def __aenter__(self) -> _FakeAsyncSession:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def commit(self) -> None:
        return None


@pytest.mark.asyncio
async def test_is_paused_helper_opens_session_and_delegates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db_base, "get_session_factory", lambda: _FakeAsyncSession)
    mock_is_paused = AsyncMock(return_value=True)
    monkeypatch.setattr(mp_module, "is_paused", mock_is_paused)

    orch = _make_orch()
    result = await orch._is_paused(PauseScope.DISPATCH)

    assert result is True
    mock_is_paused.assert_awaited_once()
    call = mock_is_paused.await_args
    assert call is not None
    assert call.args[1] is PauseScope.DISPATCH


# ---------------------------------------------------------------------------
# THE TRAP: a dispatch pause must narrow the reaper, not bypass it entirely
# -- a genuinely wedged container must still be killed (DEFECT 1).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reap_stale_claims_still_runs_the_reap_when_dispatch_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The reap must never be bypassed entirely while paused -- only its
    DB-level unclaim step is pause-sensitive (see ``_reap_with_service``'s
    ``dispatch_paused`` kwarg below). Reverting to the old full-skip makes
    this fail because ``_reap_with_service`` would never be awaited at all."""
    monkeypatch.setattr(db_base, "get_session_factory", lambda: _FakeAsyncSession)
    monkeypatch.setattr(mp_module, "is_paused", AsyncMock(return_value=True))

    orch = _make_orch()
    reap_mock = AsyncMock()
    sandbox_mock = AsyncMock()
    monkeypatch.setattr(orch, "_reap_with_service", reap_mock)
    monkeypatch.setattr(orch, "_sandbox_janitor_sweep", sandbox_mock)

    await orch._reap_stale_claims()

    reap_mock.assert_awaited_once()
    assert reap_mock.await_args is not None
    assert reap_mock.await_args.kwargs["dispatch_paused"] is True
    sandbox_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_reap_stale_claims_runs_when_not_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(db_base, "get_session_factory", lambda: _FakeAsyncSession)
    monkeypatch.setattr(mp_module, "is_paused", AsyncMock(return_value=False))

    orch = _make_orch()
    reap_mock = AsyncMock()
    monkeypatch.setattr(orch, "_reap_with_service", reap_mock)
    monkeypatch.setattr(orch, "_sandbox_janitor_sweep", AsyncMock())

    await orch._reap_stale_claims()

    reap_mock.assert_awaited_once()
    assert reap_mock.await_args is not None
    assert reap_mock.await_args.kwargs["dispatch_paused"] is False


def _grok_instance() -> AgentInstance:
    cfg = type("C", (), {"provider_type": "grok"})()
    return AgentInstance(agent_id="be-dev-1", state=AgentState.ACTIVE, config=cfg)


@pytest.mark.asyncio
async def test_reap_with_service_kills_wedged_container_even_while_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEFECT 1 (the blocker): a live-but-wedged grok container is killed
    regardless of ``dispatch_paused`` -- only the DB-level unclaim after it
    is pause-sensitive. Reverting the narrow gate (skipping the whole reap
    again while paused) makes this fail because ``_remove_container`` would
    never be awaited."""
    now = datetime.now(UTC)
    task_id = uuid4()
    wedged = type(
        "T",
        (),
        {
            "id": task_id,
            "last_heartbeat_at": now - timedelta(seconds=1200),
            "assigned_to": AGENT_UUIDS["be-dev-1"],
            "claimed_by": None,
        },
    )()

    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch, "_maybe_recover_broken_gateway", AsyncMock(return_value=False)
    )
    orch._claim_heartbeat_ttl = 300
    orch._grok_idle_kill_ttl = 900
    orch._instances = {"be-dev-1": _grok_instance()}
    remove_mock = AsyncMock()
    monkeypatch.setattr(orch, "_remove_container", remove_mock)
    svc = AsyncMock()
    svc.list_in_progress_or_claimed.return_value = [wedged]
    svc.unclaim_for_reaper = AsyncMock()

    await orch._reap_with_service(svc, dispatch_paused=True)

    remove_mock.assert_awaited_once_with(
        "roboco-agent-be-dev-1", stop_reason="reaper_wedged_grok"
    )
    assert "be-dev-1" not in orch._instances  # evicted despite the pause
    svc.unclaim_for_reaper.assert_not_awaited()  # DB-level release stays gated


@pytest.mark.asyncio
async def test_reap_with_service_unclaims_dead_claim_when_not_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sanity/regression pair for the test above: ``dispatch_paused=False``
    (the normal tick) still unclaims a genuinely dead claim exactly as
    before this drill."""
    now = datetime.now(UTC)
    task_id = uuid4()
    dead = type(
        "T",
        (),
        {
            "id": task_id,
            "last_heartbeat_at": now - timedelta(seconds=600),
            "assigned_to": AGENT_UUIDS["be-dev-2"],
            "claimed_by": None,
        },
    )()

    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    monkeypatch.setattr(
        orch, "_maybe_recover_broken_gateway", AsyncMock(return_value=False)
    )
    orch._claim_heartbeat_ttl = 300
    orch._instances = {}
    svc = AsyncMock()
    svc.list_in_progress_or_claimed.return_value = [dead]
    svc.unclaim_for_reaper = AsyncMock()

    await orch._reap_with_service(svc, dispatch_paused=False)

    svc.unclaim_for_reaper.assert_awaited_once_with(task_id)


# ---------------------------------------------------------------------------
# _dispatch_pm_work: dispatch-scope pause skips PM routing but never the
# independently-gated board-program branch.
# ---------------------------------------------------------------------------


def _pm_task(**overrides: Any) -> dict[str, Any]:
    task: dict[str, Any] = {
        "id": str(uuid4()),
        "status": "pending",
        "team": "backend",
        "title": "Do the thing",
        "assigned_to": "be-pm",
        "source": None,
        "orchestration_markers": None,
    }
    task.update(overrides)
    return task


@pytest.mark.asyncio
async def test_dispatch_pm_work_skips_pm_assigned_routing_when_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _make_orch()
    task = _pm_task()
    monkeypatch.setattr(orch, "_fetch_tasks", AsyncMock(return_value=[task]))
    monkeypatch.setattr(orch, "_is_task_handled_this_tick", lambda _id: False)
    monkeypatch.setattr(orch, "_is_paused", AsyncMock(return_value=True))
    monkeypatch.setattr(orch, "_resolve_agent_slug", lambda x: x)
    handle_pm = AsyncMock()
    handle_board = AsyncMock()
    monkeypatch.setattr(orch, "_handle_pm_assigned_task", handle_pm)
    monkeypatch.setattr(orch, "_handle_board_assigned_task", handle_board)

    async with httpx.AsyncClient() as client:
        await orch._dispatch_pm_work(client=client)

    handle_pm.assert_not_awaited()
    handle_board.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_pm_work_routes_pm_assigned_task_when_not_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _make_orch()
    task = _pm_task()
    monkeypatch.setattr(orch, "_fetch_tasks", AsyncMock(return_value=[task]))
    monkeypatch.setattr(orch, "_is_task_handled_this_tick", lambda _id: False)
    monkeypatch.setattr(orch, "_is_paused", AsyncMock(return_value=False))
    monkeypatch.setattr(orch, "_resolve_agent_slug", lambda x: x)
    handle_pm = AsyncMock()
    monkeypatch.setattr(orch, "_handle_pm_assigned_task", handle_pm)

    async with httpx.AsyncClient() as client:
        await orch._dispatch_pm_work(client=client)

    handle_pm.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_pm_work_skips_unassigned_routing_when_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _make_orch()
    task = _pm_task(assigned_to=None)
    monkeypatch.setattr(orch, "_fetch_tasks", AsyncMock(return_value=[task]))
    monkeypatch.setattr(orch, "_is_task_handled_this_tick", lambda _id: False)
    monkeypatch.setattr(orch, "_is_paused", AsyncMock(return_value=True))
    route_mock = AsyncMock()
    monkeypatch.setattr(orch, "_route_unassigned_pm_task", route_mock)

    async with httpx.AsyncClient() as client:
        await orch._dispatch_pm_work(client=client)

    route_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_pm_work_still_dispatches_board_program_when_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A dispatch-scope pause must never suppress board-program exploration
    dispatch: that is board_programs scope's own, independent job."""
    orch = _make_orch()
    task = _pm_task(source=ROADMAP_SOURCE)
    monkeypatch.setattr(orch, "_fetch_tasks", AsyncMock(return_value=[task]))
    monkeypatch.setattr(orch, "_is_task_handled_this_tick", lambda _id: False)
    monkeypatch.setattr(orch, "_is_paused", AsyncMock(return_value=True))
    handle_pm = AsyncMock()
    monkeypatch.setattr(orch, "_handle_pm_assigned_task", handle_pm)
    board_dispatch = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "roboco.runtime.orchestrator._dispatch_board_program_exploration",
        board_dispatch,
    )

    async with httpx.AsyncClient() as client:
        await orch._dispatch_pm_work(client=client)

    board_dispatch.assert_awaited_once()
    handle_pm.assert_not_awaited()  # board branch owns this task, not PM routing


# ---------------------------------------------------------------------------
# _dispatch_board_program_exploration: board_programs-scope pause suppresses
# the explorer spawn but still reports the task as handled (never falls
# through to the two-reviewer board-review path).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_board_program_exploration_skips_spawn_when_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _make_orch()
    is_paused_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(orch, "_is_paused", is_paused_mock)
    spawn = AsyncMock()
    monkeypatch.setattr(orch, "_dispatch_roadmap_exploration", spawn)
    task = {"id": str(uuid4()), "source": ROADMAP_SOURCE}

    handled = await _dispatch_board_program_exploration(orch, task)

    assert handled is True
    spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_board_program_exploration_dispatches_when_not_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _make_orch()
    monkeypatch.setattr(orch, "_is_paused", AsyncMock(return_value=False))
    spawn = AsyncMock()
    monkeypatch.setattr(orch, "_dispatch_roadmap_exploration", spawn)
    task = {"id": str(uuid4()), "source": ROADMAP_SOURCE}

    handled = await _dispatch_board_program_exploration(orch, task)

    assert handled is True
    spawn.assert_awaited_once_with(task)


@pytest.mark.asyncio
async def test_board_program_exploration_returns_false_for_non_program_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _make_orch()
    is_paused_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(orch, "_is_paused", is_paused_mock)
    task = {"id": str(uuid4()), "source": "some_other_source"}

    handled = await _dispatch_board_program_exploration(orch, task)

    assert handled is False
    is_paused_mock.assert_not_awaited()  # not even consulted for a non-program task


# ---------------------------------------------------------------------------
# _dispatch_all_work: dispatch-scope pause drains every spawn-issuing
# dispatcher except pm_work.
# ---------------------------------------------------------------------------

_DELIVERY_DISPATCHER_NAMES = (
    "_dispatch_pm_closure_work",
    "_dispatch_revision_coordination_roots",
    "_dispatch_dev_work",
    "_dispatch_qa_work",
    "_dispatch_pr_review_work",
    "_dispatch_pr_gate_work",
    "_dispatch_doc_work",
    "_dispatch_pm_review_work",
    "_dispatch_marketing_work",
    "_dispatch_blocker_work",
    "_dispatch_claimed_without_agent",
    "_dispatch_escalation_work",
    "_dispatch_approval_work",
    "_dispatch_a2a_work",
    "_dispatch_audit_work",
    "_dispatch_vault_curation_work",
    "_detect_stuck_tasks",
)


@pytest.mark.asyncio
async def test_dispatch_all_work_skips_delivery_dispatchers_when_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _make_orch()
    monkeypatch.setattr(orch, "_reap_stale_claims", AsyncMock())
    monkeypatch.setattr(orch, "_enforce_grok_cost_budget", AsyncMock())
    monkeypatch.setattr(orch, "_is_paused", AsyncMock(return_value=True))
    pm_work = AsyncMock()
    monkeypatch.setattr(orch, "_dispatch_pm_work", pm_work)
    mocks = {name: AsyncMock() for name in _DELIVERY_DISPATCHER_NAMES}
    for name, mock in mocks.items():
        monkeypatch.setattr(orch, name, mock)

    await orch._dispatch_all_work()

    for mock in mocks.values():
        mock.assert_not_awaited()
    pm_work.assert_awaited_once()


@pytest.mark.asyncio
async def test_dispatch_all_work_runs_delivery_dispatchers_when_not_paused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    orch = _make_orch()
    monkeypatch.setattr(orch, "_reap_stale_claims", AsyncMock())
    monkeypatch.setattr(orch, "_enforce_grok_cost_budget", AsyncMock())
    monkeypatch.setattr(orch, "_is_paused", AsyncMock(return_value=False))
    monkeypatch.setattr(orch, "_dispatch_pm_work", AsyncMock())
    mocks = {name: AsyncMock() for name in _DELIVERY_DISPATCHER_NAMES}
    for name, mock in mocks.items():
        monkeypatch.setattr(orch, name, mock)

    await orch._dispatch_all_work()

    for mock in mocks.values():
        mock.assert_awaited_once()
