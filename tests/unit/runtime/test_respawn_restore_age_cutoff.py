"""Respawn counters restored at startup expire with the breaker's cooldown.

Live 2026-10-28 05:15:33 the orchestrator restored 36 respawn counters ("count":
36, "evicted": 0); the previous run in the orchestrator log had stopped on
2026-09-29 21:25. Nothing aged a counter out: the restore kept every row whose
task was still open, re-stamped ``last_check`` to the restore time, and the
shutdown flush wrote every in-memory record back with ``updated_at`` = now, so
a counter from any earlier run looked fresh forever. At 05:20:06 be-qa's very
first attempt on 5db9054b of the run "tripped" with spawn_attempts 4 and paged
the CEO; fe-qa on 86ed0eab did the same a second later.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator
from sqlalchemy.dialects import postgresql

_TASK_ID = UUID("5db9054b-503d-475b-a4f4-7b74b6acfc16")
_PREVIOUS_RUN_STOPPED = datetime(2026, 9, 29, 21, 25, 33, tzinfo=UTC)
_RESTORED_AT = datetime(2026, 10, 28, 5, 15, 33, tzinfo=UTC)
_ONE_SHORT_OF_TRIP = AgentOrchestrator._PM_RESPAWN_MAX_UNPRODUCTIVE
_COOLDOWN = AgentOrchestrator._PM_RESPAWN_TRIP_COOLDOWN_SECONDS


def _row(updated_at: datetime) -> SimpleNamespace:
    """A ``respawn_tracker`` row one strike short of the trip."""
    return SimpleNamespace(
        agent_slug="be-qa",
        task_id=_TASK_ID,
        count=_ONE_SHORT_OF_TRIP,
        last_status="awaiting_qa",
        last_check=updated_at,
        tracing_resets=0,
        revisit_resets=0,
        notified=False,
        updated_at=updated_at,
    )


def _session_factory(rows: list[Any]) -> Any:
    """``get_session_factory()`` stub: respawn rows, then the live task, then
    a throwaway result for each eviction delete."""
    db = AsyncMock()
    rows_result = MagicMock()
    rows_result.scalars.return_value.all.return_value = rows
    live_result = MagicMock()
    live_result.all.return_value = [SimpleNamespace(id=_TASK_ID, status="awaiting_qa")]
    db.execute = AsyncMock(side_effect=[rows_result, live_result, MagicMock()])
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=ctx)


def _orchestrator() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    orch._pm_respawn_tracker = {}
    orch._bg_tasks = set()
    orch._respawn_persist_lock = asyncio.Lock()
    return orch


@contextlib.contextmanager
def _quiet_breaker_side_effects(orch: AgentOrchestrator) -> Any:
    orch_any = cast("Any", orch)
    orch_any._schedule_respawn_persist = MagicMock()
    orch_any._review_claimant_slug = AsyncMock(return_value=None)
    orch_any._pm_respawn_spawn_anyway = AsyncMock(return_value=False)
    orch_any._pm_trip_stall_notice = AsyncMock()
    audit = AsyncMock()
    audit.has_recent_tracing_gap = AsyncMock(return_value=False)
    with patch("roboco.services.audit.get_audit_service", return_value=audit):
        yield


def test_counter_from_a_run_a_month_earlier_is_evicted() -> None:
    restored, stale = AgentOrchestrator._partition_respawn_rows(
        [_row(_PREVIOUS_RUN_STOPPED)],
        {_TASK_ID: "awaiting_qa"},
        now=_RESTORED_AT,
        max_age_seconds=_COOLDOWN,
    )

    assert restored == {}
    assert stale == [("be-qa", _TASK_ID)]


def test_counter_struck_within_the_cooldown_is_restored_with_its_age() -> None:
    struck_at = _RESTORED_AT - timedelta(seconds=_COOLDOWN - 60)

    restored, stale = AgentOrchestrator._partition_respawn_rows(
        [_row(struck_at)],
        {_TASK_ID: "awaiting_qa"},
        now=_RESTORED_AT,
        max_age_seconds=_COOLDOWN,
    )

    assert stale == []
    entry = restored[("be-qa", str(_TASK_ID))]
    assert entry["count"] == _ONE_SHORT_OF_TRIP
    assert entry["updated_at"] == struck_at


@pytest.mark.asyncio
async def test_first_attempt_after_restoring_a_month_old_counter_does_not_trip() -> (
    None
):
    """The logged sequence: restore, then be-qa's first attempt on 5db9054b."""
    orch = _orchestrator()
    factory = _session_factory([_row(_PREVIOUS_RUN_STOPPED)])
    with patch("roboco.db.base.get_session_factory", return_value=factory):
        await orch.restore_respawn_tracker()

    with _quiet_breaker_side_effects(orch):
        gated = await orch._pm_respawn_should_gate(
            "be-qa", {"id": str(_TASK_ID), "status": "awaiting_qa"}
        )

    assert gated is False
    cast("Any", orch)._pm_trip_stall_notice.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_fresh_restored_counter_still_trips_on_its_next_attempt() -> None:
    """Restart-loop protection is unchanged inside the cooldown window."""
    orch = _orchestrator()
    factory = _session_factory([_row(datetime.now(UTC) - timedelta(seconds=30))])
    with patch("roboco.db.base.get_session_factory", return_value=factory):
        await orch.restore_respawn_tracker()

    with _quiet_breaker_side_effects(orch):
        gated = await orch._pm_respawn_should_gate(
            "be-qa", {"id": str(_TASK_ID), "status": "awaiting_qa"}
        )

    assert gated is True


def test_every_counter_mutation_stamps_its_own_time() -> None:
    orch = _orchestrator()
    cast("Any", orch)._schedule_bg = lambda coro: coro.close()
    record: dict[str, Any] = {"count": 2, "last_check": _RESTORED_AT}
    before = datetime.now(UTC)

    orch._schedule_respawn_persist("be-qa", str(_TASK_ID), record)

    assert record["updated_at"] >= before


@pytest.mark.asyncio
async def test_persisting_an_untouched_restored_counter_keeps_its_strike_time() -> None:
    """The shutdown flush re-persists every in-memory record; a counter that
    was restored and never struck again must keep its original time."""
    orch = _orchestrator()
    written: dict[str, Any] = {}

    async def _execute(stmt: Any) -> Any:
        written.update(stmt.compile(dialect=postgresql.dialect()).params)
        return MagicMock()

    db = MagicMock()
    db.execute = _execute
    db.commit = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    with patch(
        "roboco.db.base.get_session_factory", return_value=MagicMock(return_value=ctx)
    ):
        await orch._persist_respawn_record(
            "be-qa",
            str(_TASK_ID),
            {
                "count": _ONE_SHORT_OF_TRIP,
                "last_check": _RESTORED_AT,
                "updated_at": _PREVIOUS_RUN_STOPPED,
            },
        )

    assert written["updated_at"] == _PREVIOUS_RUN_STOPPED
