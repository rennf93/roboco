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
import roboco.db.base as db_base
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


def _gate_task() -> dict[str, Any]:
    return {"id": str(uuid4()), "status": "awaiting_qa"}


@pytest.mark.asyncio
async def test_gate_skips_without_strike_while_review_claim_live() -> None:
    """An awaiting_qa task with a live review claim: the spawn is skipped and
    NO strike accrues - a long review must not trip the breaker mid-review."""
    orch = _gate_orch()
    task = _gate_task()
    orch._pm_respawn_tracker[("fe-qa", task["id"])] = {
        "count": 3,
        "last_status": "awaiting_qa",
        "last_check": datetime.now(UTC),
        "seen_statuses": ["awaiting_qa"],
    }
    orch._review_claim_is_live = AsyncMock(return_value=True)

    gated = await orch._pm_respawn_should_gate("fe-qa", task)

    assert gated is True
    assert orch._pm_respawn_tracker[("fe-qa", task["id"])]["count"] == 3  # noqa: PLR2004
    orch._pm_trip_stall_notice.assert_not_awaited()


@pytest.mark.asyncio
async def test_gate_counts_strike_when_review_claim_dead() -> None:
    """A dead review claim falls through to the strike logic - the breaker
    stays authoritative for genuinely unproductive spawn loops."""
    orch = _gate_orch()
    task = _gate_task()
    orch._pm_respawn_tracker[("fe-qa", task["id"])] = {
        "count": 1,
        "last_status": "awaiting_qa",
        "last_check": datetime.now(UTC),
        "seen_statuses": ["awaiting_qa"],
    }
    orch._review_claim_is_live = AsyncMock(return_value=False)

    gated = await orch._pm_respawn_should_gate("fe-qa", task)

    assert gated is False
    assert orch._pm_respawn_tracker[("fe-qa", task["id"])]["count"] == 2  # noqa: PLR2004


@pytest.mark.asyncio
async def test_review_claim_is_live_requires_fresh_heartbeat() -> None:
    """_review_claim_is_live: claimant + heartbeat within TTL = live; a stale
    or missing heartbeat is not live even with a claimant id set."""

    def _factory(db: Any) -> Any:
        class _Result:
            def __init__(self, value: Any) -> None:
                self._value = value

            def scalar_one_or_none(self) -> Any:
                return self._value

        class _DB:
            async def execute(self, *_a: Any, **_k: Any) -> _Result:
                return _Result(db)

        return _DB

    orch = _gate_orch()
    orch._review_claim_heartbeat_ttl = 3600
    fresh = datetime.now(UTC)
    frozen = datetime.now(UTC) - timedelta(hours=2)

    # Build a session-factory stand-in whose async context manager yields
    # the stub DB returning the given claimant value.

    def _patch(monkeypatch: pytest.MonkeyPatch, claimant: Any) -> None:
        class _Ctx:
            async def __aenter__(self) -> Any:
                return _factory(claimant)

            async def __aexit__(self, *_a: Any) -> None:
                return None

        def _session_factory() -> Any:
            return _Ctx

        monkeypatch.setattr(db_base, "get_session_factory", _session_factory)

    with pytest.MonkeyPatch.context() as mp:
        _patch(mp, uuid4())
        assert await orch._review_claim_is_live(uuid4()) is True
    with pytest.MonkeyPatch.context() as mp:
        _patch(mp, None)
        assert await orch._review_claim_is_live(uuid4()) is False
    del fresh, frozen
