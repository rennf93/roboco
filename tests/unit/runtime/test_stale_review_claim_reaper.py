"""Reaper releases stale awaiting_* review claims whose claimant went silent.

Live production, 2026-09-28: fe-qa claimed the review of 99f61c8c at 06:35,
its run died (breaker_tripped), and the claim sat HELD for 13.5 hours - the
task stayed awaiting_qa, competing claimants were rejected, and the dependent
dfeb2d8a bounced behind it. The existing reaper only sweeps claimed /
in_progress rows, and the i_am_idle exit-release (3075facb) only fires on a
CLEAN exit - a claimant that DIES mid-review had no backstop at all (the gap
``TaskService.list_open_review_claims`` documents).

The review-claim sweep closes it: ``_reap_one_stale_review_claim`` releases
an awaiting_* claim whose claimant's heartbeat is stale past
``review_claim_reap_seconds`` (much longer than the code-claim TTL because a
reviewer legitimately fires no gateway verb between claim_review and its
pass/fail decision), with three escape hatches: a claimant alive and busy on
a fresher claim elsewhere (parked), a provider-parked claimant (the
probe-resume loop owns recovery), and the dispatch maintenance pause (a
release only helps if a re-claim can follow).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from roboco.models.base import TaskStatus
from roboco.models.runtime import WaitingRecord
from roboco.runtime.orchestrator import AgentOrchestrator
from roboco.services.task import TaskService

STALE = timedelta(hours=13, minutes=30)
TTL = 3600


def _task(task_id: UUID | None = None, **over: Any) -> Any:
    now = datetime.now(UTC)
    base: dict[str, Any] = {
        "id": task_id or uuid4(),
        "title": "review me",
        "status": TaskStatus.AWAITING_QA,
        "assigned_to": "fe-qa",
        "claimed_by": "fe-qa",
        "claimed_at": now - STALE,
        "last_heartbeat_at": now - STALE,
    }
    base.update(over)
    return type("T", (), base)()


def _orch() -> Any:
    # Any-typed: the harness swaps orchestrator METHODS for AsyncMocks
    # (notification/kill/skip spies), which mypy's method-assign would
    # otherwise reject on the strongly-typed engine - the __new__ harness
    # pattern used across the reaper suites is deliberately duck-typed.
    orch = cast("Any", AgentOrchestrator.__new__(AgentOrchestrator))
    orch._instances = {}
    orch._claim_heartbeat_ttl = 600
    orch._review_claim_heartbeat_ttl = TTL
    return orch


def _svc() -> AsyncMock:
    svc = AsyncMock()
    svc.release_review_claim_for_reaper = AsyncMock()
    return svc


# ---------------------------------------------------------------------------
# _reap_one_stale_review_claim
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dead_claimant_released() -> None:
    """A 13.5h-stale review claim with a dead claimant is released."""
    orch = _orch()
    orch._notify_stale_review_claim_released = AsyncMock()
    claim = _task()
    svc = _svc()

    await orch._reap_one_stale_review_claim(svc, claim, False, [claim])

    svc.release_review_claim_for_reaper.assert_awaited_once_with(claim.id)
    orch._notify_stale_review_claim_released.assert_awaited_once()


@pytest.mark.asyncio
async def test_parked_claim_released_when_busy_elsewhere() -> None:
    """fe-qa's exact shape: reviewing a FRESH task while holding a stale
    second review claim - the stale one is released without touching the
    container (it is healthily working the other review)."""
    orch = _orch()
    orch._notify_stale_review_claim_released = AsyncMock()
    now = datetime.now(UTC)
    stale = _task(last_heartbeat_at=now - STALE)
    fresh = _task(
        assigned_to="fe-qa",
        claimed_by="fe-qa",
        claimed_at=now - timedelta(minutes=5),
        last_heartbeat_at=now - timedelta(seconds=30),
    )
    svc = _svc()

    await orch._reap_one_stale_review_claim(svc, stale, False, [stale, fresh])

    svc.release_review_claim_for_reaper.assert_awaited_once_with(stale.id)


@pytest.mark.asyncio
async def test_parked_release_never_kills_the_container() -> None:
    """The parked path must decide BEFORE the wedged/stuck-kill machinery:
    a live claimant busy elsewhere is working, not wedged."""
    orch = _orch()
    orch._notify_stale_review_claim_released = AsyncMock()
    now = datetime.now(UTC)
    stale = _task(last_heartbeat_at=now - STALE)
    fresh = _task(
        assigned_to="fe-qa",
        claimed_by="fe-qa",
        claimed_at=now - timedelta(minutes=5),
        last_heartbeat_at=now - timedelta(seconds=30),
    )
    svc = _svc()
    kill = AsyncMock()
    orch._maybe_kill_stuck_claude = kill
    orch._maybe_kill_wedged_grok = kill

    await orch._reap_one_stale_review_claim(svc, stale, False, [stale, fresh])

    kill.assert_not_awaited()
    svc.release_review_claim_for_reaper.assert_awaited_once()


@pytest.mark.asyncio
async def test_fresh_claim_untouched() -> None:
    """A review claim whose heartbeat is within the review TTL survives."""
    orch = _orch()
    now = datetime.now(UTC)
    claim = _task(last_heartbeat_at=now - timedelta(seconds=600))
    svc = _svc()

    await orch._reap_one_stale_review_claim(svc, claim, False, [claim])

    svc.release_review_claim_for_reaper.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_pause_defers_release() -> None:
    """Paused fleet: no re-claim can follow this tick, so the release is
    deferred - the parked, dead, and live paths all defer uniformly."""
    orch = _orch()
    orch._notify_stale_review_claim_released = AsyncMock()
    claim = _task()
    svc = _svc()

    await orch._reap_one_stale_review_claim(svc, claim, True, [claim])

    svc.release_review_claim_for_reaper.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_parked_claimant_untouched() -> None:
    """A rate-limit-parked claimant is OFFLINE by design; the probe-resume
    loop owns its recovery, so the reaper must not strip its claim."""
    orch = _orch()
    claim = _task()
    orch._waiting_records = {
        "fe-qa": WaitingRecord(
            agent_id="fe-qa",
            task_id=str(claim.id),
            waiting_for="rate_limit_lifted",
            waiting_since=datetime.now(UTC),
        )
    }
    svc = _svc()

    await orch._reap_one_stale_review_claim(svc, claim, False, [claim])

    svc.release_review_claim_for_reaper.assert_not_awaited()


@pytest.mark.asyncio
async def test_live_claimant_skip_wins_when_not_parked() -> None:
    """A live claimant holding ONLY this stale claim is left to the
    wedged/stuck-kill machinery (_should_skip_live_reap) - not silently
    released out from under a container the registry still tracks."""
    orch = _orch()
    skip = AsyncMock(return_value=True)
    orch._should_skip_live_reap = skip
    claim = _task()
    svc = _svc()

    await orch._reap_one_stale_review_claim(svc, claim, False, [claim])

    skip.assert_awaited_once()
    svc.release_review_claim_for_reaper.assert_not_awaited()


@pytest.mark.asyncio
async def test_parked_release_deferred_during_pause() -> None:
    """Same parked shape as the release test, but dispatch paused: the
    release is deferred and the container is not killed either."""
    orch = _orch()
    now = datetime.now(UTC)
    stale = _task(last_heartbeat_at=now - STALE)
    fresh = _task(
        assigned_to="fe-qa",
        claimed_by="fe-qa",
        claimed_at=now - timedelta(minutes=5),
        last_heartbeat_at=now - timedelta(seconds=30),
    )
    svc = _svc()
    kill = AsyncMock()
    orch._maybe_kill_stuck_claude = kill
    orch._maybe_kill_wedged_grok = kill

    await orch._reap_one_stale_review_claim(svc, stale, True, [stale, fresh])

    svc.release_review_claim_for_reaper.assert_not_awaited()
    kill.assert_not_awaited()


@pytest.mark.asyncio
async def test_sweep_wires_the_review_pass() -> None:
    """_reap_with_service feeds list_all_open_review_claims rows through the
    review sweep (wiring guard: the compose-claim sweep alone misses them)."""
    orch = _orch()
    orch._notify_stale_review_claim_released = AsyncMock()
    claim = _task()
    svc = _svc()
    svc.list_in_progress_or_claimed = AsyncMock(return_value=[])
    svc.list_all_open_review_claims = AsyncMock(return_value=[claim])

    await orch._reap_with_service(svc)

    svc.release_review_claim_for_reaper.assert_awaited_once_with(claim.id)


# ---------------------------------------------------------------------------
# TaskService.release_review_claim_for_reaper
# ---------------------------------------------------------------------------


def _service_with(task: Any) -> tuple[Any, AsyncMock]:
    # Any-typed for the same method-assign reason as _orch (get is swapped
    # for an AsyncMock).
    svc = cast("Any", TaskService.__new__(TaskService))
    flush = AsyncMock()
    svc.session = type("S", (), {"flush": flush})()
    svc.get = AsyncMock(return_value=task)
    return svc, flush


def _claim_row(status: TaskStatus = TaskStatus.AWAITING_QA) -> Any:
    claimant = uuid4()
    return type(
        "T",
        (),
        {
            "id": uuid4(),
            "status": status,
            "active_claimant_id": claimant,
            "claimed_by": claimant,
            "claimed_at": datetime.now(UTC),
            "last_heartbeat_at": datetime.now(UTC),
        },
    )()


@pytest.mark.asyncio
async def test_service_release_clears_claim_fields_keeps_status() -> None:
    """The release clears the claimant lock exactly as the decision verbs do;
    status stays awaiting_qa and the row is flushed."""
    task = _claim_row()
    svc, flush = _service_with(task)

    await svc.release_review_claim_for_reaper(task.id)

    assert task.active_claimant_id is None
    assert task.claimed_by is None
    assert task.claimed_at is None
    assert task.last_heartbeat_at is None
    assert task.status == TaskStatus.AWAITING_QA
    flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_service_release_noop_on_non_review_status() -> None:
    """A claimed (code) row must go through unclaim_for_reaper instead - the
    review release must never clear a code claim's fields."""
    task = _claim_row(status=TaskStatus.IN_PROGRESS)
    svc, flush = _service_with(task)
    before = (task.active_claimant_id, task.last_heartbeat_at)

    await svc.release_review_claim_for_reaper(task.id)

    assert (task.active_claimant_id, task.last_heartbeat_at) == before
    flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_service_release_noop_on_missing_task() -> None:
    svc = cast("Any", TaskService.__new__(TaskService))
    flush = AsyncMock()
    svc.session = type("S", (), {"flush": flush})()
    svc.get = AsyncMock(return_value=None)

    await svc.release_review_claim_for_reaper(uuid4())

    flush.assert_not_awaited()


# ---------------------------------------------------------------------------
# Breaker: a live review claim is progress, not a strike
# (fe-qa tripped on 372eac39 on 2026-09-29 while actively reviewing)
# ---------------------------------------------------------------------------


def _gate_orch() -> Any:
    orch = _orch()
    orch._pm_respawn_tracker = {}
    orch._PM_RESPAWN_MAX_UNPRODUCTIVE = 3
    orch._schedule_respawn_persist = lambda *_a, **_k: None
    orch._respawn_status_change_resets = lambda *_a, **_k: False
    orch._pm_tracing_gap_reset = AsyncMock(return_value=False)
    orch._pm_cooldown_gate = lambda *_a, **_k: None  # sync in production
    orch._pm_respawn_spawn_anyway = AsyncMock(return_value=False)
    orch._pm_trip_stall_notice = AsyncMock()
    return orch


_AT_TRIP = 3  # one strike below the trip threshold
_ONE_STRIKE = 2


def _gate_task() -> dict[str, Any]:
    return {"id": str(uuid4()), "status": "awaiting_qa"}


@pytest.mark.asyncio
async def test_gate_skips_without_strike_while_review_showing_work() -> None:
    """An awaiting_qa task whose claimant is producing varied tool work: the
    spawn is skipped and NO strike accrues - a long review must not trip the
    breaker mid-review, regardless of its frozen claim heartbeat."""
    orch = _gate_orch()
    task = _gate_task()
    orch._pm_respawn_tracker[("fe-qa", task["id"])] = {
        "count": _AT_TRIP,
        "last_status": "awaiting_qa",
        "last_check": datetime.now(UTC),
        "seen_statuses": ["awaiting_qa"],
    }
    orch._review_claimant_slug = AsyncMock(return_value="fe-qa")
    orch._agent_work_signal = AsyncMock(return_value="working")

    gated = await orch._pm_respawn_should_gate("fe-qa", task)

    assert gated is True
    assert orch._pm_respawn_tracker[("fe-qa", task["id"])]["count"] == _AT_TRIP
    orch._pm_trip_stall_notice.assert_not_awaited()


@pytest.mark.asyncio
async def test_gate_counts_strike_when_review_is_thrashing() -> None:
    """A claimant stuck in a retry loop (one verb dominating, errors
    everywhere - the be-qa evidence loop) is NOT shielded: the breaker must
    trip on thrash even though the repeated gateway verbs keep the heartbeat
    nominally fresh."""
    orch = _gate_orch()
    task = _gate_task()
    orch._pm_respawn_tracker[("be-qa", task["id"])] = {
        "count": _AT_TRIP,
        "last_status": "awaiting_qa",
        "last_check": datetime.now(UTC),
        "seen_statuses": ["awaiting_qa"],
    }
    orch._review_claimant_slug = AsyncMock(return_value="be-qa")
    orch._agent_work_signal = AsyncMock(return_value="thrashing")

    gated = await orch._pm_respawn_should_gate("be-qa", task)

    assert gated is True  # tripped: thrash is a strike, and it was the last one
    assert orch._pm_respawn_tracker[("be-qa", task["id"])]["count"] == _AT_TRIP + 1


@pytest.mark.asyncio
async def test_gate_counts_strike_when_review_container_idle() -> None:
    """A claimant with an idle container (gone, or zero tool events in the
    window) is not working: the strike path applies."""
    orch = _gate_orch()
    task = _gate_task()
    orch._pm_respawn_tracker[("fe-qa", task["id"])] = {
        "count": 1,
        "last_status": "awaiting_qa",
        "last_check": datetime.now(UTC),
        "seen_statuses": ["awaiting_qa"],
    }
    orch._review_claimant_slug = AsyncMock(return_value="fe-qa")
    orch._agent_work_signal = AsyncMock(return_value="idle")

    gated = await orch._pm_respawn_should_gate("fe-qa", task)

    assert gated is False
    assert orch._pm_respawn_tracker[("fe-qa", task["id"])]["count"] == 2  # noqa: PLR2004


@pytest.mark.asyncio
async def test_gate_not_gated_for_non_review_status() -> None:
    """The work-signal guard only arms on awaiting_* tasks - a claimed
    in_progress code task keeps the legacy strike behavior untouched and
    never even consults the claimant's tool signal."""
    orch = _gate_orch()
    task = {"id": str(uuid4()), "status": "in_progress"}
    orch._pm_respawn_tracker[("be-dev-1", task["id"])] = {
        "count": 1,
        "last_status": "in_progress",
        "last_check": datetime.now(UTC),
        "seen_statuses": ["in_progress"],
    }
    slug_lookup = AsyncMock(return_value="be-dev-1")
    orch._review_claimant_slug = slug_lookup

    gated = await orch._pm_respawn_should_gate("be-dev-1", task)

    assert gated is False
    slug_lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_work_signal_thrashes_on_single_tool_domination() -> None:
    """_agent_work_signal: >= 80% of recent calls being ONE tool (the be-qa
    give_me_work spam) classifies as thrashing even with zero errors."""
    orch = _gate_orch()
    log_lines = []
    for _ in range(20):
        log_lines.append(
            '{"type":"tool_execution_start","toolName":"mcp_roboco_flow_give_me_work"}'
        )
    for name in ("bash", "read", "note"):
        log_lines.append(f'{{"type":"tool_execution_start","toolName":"{name}"}}')
    orch._container_log_text = AsyncMock(return_value="\n".join(log_lines))

    assert await orch._agent_work_signal("be-qa") == "thrashing"


@pytest.mark.asyncio
async def test_work_signal_thrashes_on_error_dominance() -> None:
    """_agent_work_signal: errors dominating the recent calls (the be-qa
    evidence loop) classify as thrashing even with varied tool names."""
    orch = _gate_orch()
    log_lines = []
    for name in (
        "evidence",
        "bash",
        "read",
        "note",
        "give_me_work",
        "evidence",
        "bash",
        "read",
        "note",
        "give_me_work",
    ):
        log_lines.append(f'{{"type":"tool_execution_start","toolName":"{name}"}}')
    text = "\n".join(log_lines)
    for _ in range(8):
        text += "\nError executing tool evidence"
    orch._container_log_text = AsyncMock(return_value=text)

    assert await orch._agent_work_signal("be-qa") == "thrashing"


@pytest.mark.asyncio
async def test_work_signal_working_on_varied_success() -> None:
    """Varied tools, low error rate -> working (a real review in flight)."""
    orch = _gate_orch()
    log_lines = []
    for name in (
        "bash",
        "read",
        "note",
        "bash",
        "git_diff",
        "evidence",
        "bash",
        "read",
        "note",
        "bash",
    ):
        log_lines.append(f'{{"type":"tool_execution_start","toolName":"{name}"}}')
    text = "\n".join(log_lines) + "\nError executing tool evidence"
    orch._container_log_text = AsyncMock(return_value=text)

    assert await orch._agent_work_signal("fe-qa") == "working"


@pytest.mark.asyncio
async def test_work_signal_idle_without_tool_events() -> None:
    """No tool events in the window -> idle (nothing is happening)."""
    orch = _gate_orch()
    orch._container_log_text = AsyncMock(
        return_value='{"type":"turn_start"}\nsome prose output'
    )

    assert await orch._agent_work_signal("fe-qa") == "idle"
