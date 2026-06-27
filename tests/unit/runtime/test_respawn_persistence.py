"""The PM-respawn counter survives an orchestrator restart.

`_pm_respawn_tracker` is the loop breaker against respawning the same PM on the
same task forever. Kept only in memory it reset to count=1 on every restart,
re-burning the whole strike threshold against a still-wedged task. These tests
cover the write-through persist on each gate mutation, the startup loader (which
validates against live tasks and drops terminal/missing rows), and the safety
property that a restored counter trips at the persisted threshold — never
manufacturing a spawn.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from roboco.runtime.orchestrator import AgentOrchestrator
from sqlalchemy.dialects import postgresql

_SEEDED_COUNT = 3  # a persisted strike count, one below the trip threshold
_STRIKE_COUNT = 2
_MIN_PERSISTS = 2
_TRIP_COUNT = 4  # count > _PM_RESPAWN_MAX_UNPRODUCTIVE (3) fires the gate


def _new_orchestrator() -> AgentOrchestrator:
    orch = AgentOrchestrator.__new__(AgentOrchestrator)
    cast("Any", orch)._pm_respawn_tracker = {}
    cast("Any", orch)._bg_tasks = set()
    return orch


def _row(task_id: Any, **over: Any) -> SimpleNamespace:
    base = {
        "agent_slug": "be-pm",
        "task_id": task_id,
        "count": 2,
        "last_status": "blocked",
        "last_check": datetime(2026, 6, 26, tzinfo=UTC),
        "tracing_resets": 0,
        "notified": False,
    }
    base.update(over)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------- #
# Pure partition helper
# --------------------------------------------------------------------------- #


def test_partition_keeps_live_nonterminal_rows() -> None:
    tid = uuid4()
    rows = [_row(tid, count=3)]
    restored, stale = AgentOrchestrator._partition_respawn_rows(
        rows, {tid: "in_progress"}
    )
    assert stale == []
    assert restored[("be-pm", str(tid))]["count"] == _SEEDED_COUNT
    assert restored[("be-pm", str(tid))]["last_status"] == "blocked"


def test_partition_drops_terminal_and_missing_rows() -> None:
    done, cancelled, gone = uuid4(), uuid4(), uuid4()
    rows = [_row(done), _row(cancelled), _row(gone)]
    restored, stale = AgentOrchestrator._partition_respawn_rows(
        rows,
        {done: "completed", cancelled: "cancelled"},  # gone absent entirely
    )
    assert restored == {}
    assert {(s, t) for s, t in stale} == {
        ("be-pm", done),
        ("be-pm", cancelled),
        ("be-pm", gone),
    }


# --------------------------------------------------------------------------- #
# Startup loader
# --------------------------------------------------------------------------- #


def _mock_session_factory(respawn_rows: list[Any], live: list[Any]) -> Any:
    """A get_session_factory() stub: 1st execute -> respawn rows, 2nd -> live
    tasks, any further (deletes) -> a throwaway result."""
    db = AsyncMock()
    rows_result = MagicMock()
    rows_result.scalars.return_value.all.return_value = respawn_rows
    live_result = MagicMock()
    live_result.all.return_value = live
    db.execute = AsyncMock(side_effect=[rows_result, live_result, MagicMock()])
    db.commit = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=ctx)
    return factory, db


@pytest.mark.asyncio
async def test_loader_populates_dict_with_str_keys() -> None:
    orch = _new_orchestrator()
    tid = uuid4()
    factory, _db = _mock_session_factory(
        [_row(tid, count=3)], [SimpleNamespace(id=tid, status="in_progress")]
    )
    with patch("roboco.db.base.get_session_factory", return_value=factory):
        restored = await orch.restore_respawn_tracker()
    assert restored == 1
    assert orch._pm_respawn_tracker[("be-pm", str(tid))]["count"] == _SEEDED_COUNT


@pytest.mark.asyncio
async def test_loader_skips_terminal_rows_and_deletes_them() -> None:
    orch = _new_orchestrator()
    done = uuid4()
    factory, db = _mock_session_factory(
        [_row(done)], [SimpleNamespace(id=done, status="completed")]
    )
    with patch("roboco.db.base.get_session_factory", return_value=factory):
        restored = await orch.restore_respawn_tracker()
    assert restored == 0
    assert orch._pm_respawn_tracker == {}
    db.commit.assert_awaited()  # stale row deleted + committed


@pytest.mark.asyncio
async def test_loader_empty_on_exception() -> None:
    orch = _new_orchestrator()
    with patch(
        "roboco.db.base.get_session_factory", side_effect=RuntimeError("db down")
    ):
        restored = await orch.restore_respawn_tracker()
    assert restored == 0
    assert orch._pm_respawn_tracker == {}


# --------------------------------------------------------------------------- #
# Write-through on each gate mutation
# --------------------------------------------------------------------------- #


def _capture_persists(orch: AgentOrchestrator) -> list[tuple[str, str, dict[str, Any]]]:
    """Record each _schedule_respawn_persist call, snapshotting the payload.

    The real scheduler copies the record (dict(record)) before the background
    write, so the test must snapshot too — the gate mutates the same dict object
    in place, so capturing the reference would show only its final state.
    """
    captured: list[tuple[str, str, dict[str, Any]]] = []

    def _cap(agent_slug: str, task_id: str, record: dict[str, Any]) -> None:
        captured.append((agent_slug, task_id, dict(record)))

    cast("Any", orch)._schedule_respawn_persist = _cap
    return captured


@pytest.mark.asyncio
async def test_new_entry_and_strike_schedule_persist() -> None:
    orch = _new_orchestrator()
    captured = _capture_persists(orch)
    task_id = str(uuid4())
    task = {"id": task_id, "status": "pending"}
    fake_audit = AsyncMock()
    fake_audit.has_recent_tracing_gap = AsyncMock(return_value=False)
    with (
        patch("roboco.services.audit.get_audit_service", return_value=fake_audit),
        patch(
            "roboco.services.notification.NotificationService",
            return_value=AsyncMock(),
        ),
    ):
        await orch._pm_respawn_should_gate("be-pm", task)  # new entry
        await orch._pm_respawn_should_gate("be-pm", task)  # strike -> count 2
    assert len(captured) >= _MIN_PERSISTS
    assert captured[0][0] == "be-pm" and captured[0][1] == task_id
    assert captured[0][2]["count"] == 1
    assert captured[1][2]["count"] == _STRIKE_COUNT


@pytest.mark.asyncio
async def test_notified_flip_schedules_persist_with_notified_true() -> None:
    orch = _new_orchestrator()
    captured = _capture_persists(orch)
    task_id = str(uuid4())
    task = {"id": task_id, "status": "pending"}
    fake_audit = AsyncMock()
    fake_audit.has_recent_tracing_gap = AsyncMock(return_value=False)
    with (
        patch("roboco.services.audit.get_audit_service", return_value=fake_audit),
        patch(
            "roboco.services.notification.NotificationService",
            return_value=AsyncMock(),
        ),
    ):
        for _ in range(4):  # 4th trips the gate + flips notified
            await orch._pm_respawn_should_gate("be-pm", task)
    assert any(c[2].get("notified") for c in captured), (
        "the notified flip must schedule a persist with notified=True"
    )


@pytest.mark.asyncio
async def test_tracing_reset_schedules_persist_with_reset_count() -> None:
    orch = _new_orchestrator()
    captured = _capture_persists(orch)
    task_id = str(uuid4())
    task = {"id": task_id, "status": "blocked"}
    fake_audit = AsyncMock()
    fake_audit.has_recent_tracing_gap = AsyncMock(return_value=True)
    with patch("roboco.services.audit.get_audit_service", return_value=fake_audit):
        await orch._pm_respawn_should_gate("be-pm", task)  # new entry
        await orch._pm_respawn_should_gate("be-pm", task)  # tracing reset
    assert captured[-1][2]["count"] == 1
    assert captured[-1][2]["tracing_resets"] == 1


# --------------------------------------------------------------------------- #
# Persist helper safety
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_persist_record_swallows_db_failure() -> None:
    orch = _new_orchestrator()
    with patch(
        "roboco.db.base.get_session_factory", side_effect=RuntimeError("db down")
    ):
        # Must not raise — a persistence failure can never gate/un-gate a spawn.
        await orch._persist_respawn_record(
            "be-pm",
            str(uuid4()),
            {"count": 2, "last_check": datetime.now(UTC)},
        )


@pytest.mark.asyncio
async def test_persist_record_uses_atomic_upsert_no_delete_then_insert() -> None:
    """Wedge #3 (2026-06-27 live meltdown): the persist did delete-then-insert in
    its own transaction. A respawn loop fires count 1->2->3->4 in quick
    succession, scheduling a fire-and-forget persist per increment; two of those
    for the same (agent_slug, task_id) race in separate transactions and the
    loser's INSERT hit pk_respawn_tracker UniqueViolation, so the durable count
    stuck at the first INSERT's value and a restart re-burned the strike
    threshold — exactly the re-burn this feature was built to stop. The persist
    must be a single atomic ON CONFLICT DO UPDATE upsert (no DELETE, no db.add):
    concurrent upserts on the same key serialize at row level and never collide.
    """
    orch = _new_orchestrator()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock())
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=db)
    ctx.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=ctx)
    with patch("roboco.db.base.get_session_factory", return_value=factory):
        await orch._persist_respawn_record(
            "be-pm",
            str(uuid4()),
            {
                "count": 4,
                "last_status": "pending",
                "last_check": datetime.now(UTC),
                "tracing_resets": 0,
                "notified": True,
            },
        )
    # One atomic statement, no ORM add, no delete.
    assert db.execute.await_count == 1
    db.add.assert_not_called()
    stmt = db.execute.await_args.args[0]
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT" in sql
    assert "DO UPDATE" in sql
    assert "DELETE" not in sql.upper()


# --------------------------------------------------------------------------- #
# Safety regression + transparency
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_restored_counter_trips_at_persisted_threshold_not_from_one() -> None:
    """A restart mid-loop must NOT reset the strike count to 1."""
    orch = _new_orchestrator()
    cast("Any", orch)._schedule_respawn_persist = MagicMock()
    task_id = str(uuid4())
    # Simulate restore: count=3 (one below the trip), status-stable, resets spent.
    orch._pm_respawn_tracker[("be-pm", task_id)] = {
        "count": 3,
        "last_status": "blocked",
        "last_check": datetime(2026, 6, 26, tzinfo=UTC),
        "tracing_resets": 3,
        "notified": False,
    }
    task = {"id": task_id, "status": "blocked"}
    fake_audit = AsyncMock()
    fake_audit.has_recent_tracing_gap = AsyncMock(return_value=False)
    with (
        patch("roboco.services.audit.get_audit_service", return_value=fake_audit),
        patch(
            "roboco.services.notification.NotificationService",
            return_value=AsyncMock(),
        ),
    ):
        gated = await orch._pm_respawn_should_gate("be-pm", task)
    assert gated is True  # fires on the next spawn, not re-counted from 1
    assert orch._pm_respawn_tracker[("be-pm", task_id)]["count"] == _TRIP_COUNT


@pytest.mark.asyncio
async def test_restart_midloop_continues_identically_to_no_restart() -> None:
    """Transparency: the gate decision depends only on the dict contents.

    Drive a fresh orchestrator through N spawns; separately drive a second one
    for K spawns, snapshot its dict (the restart point), load that snapshot into
    a third orchestrator and continue — the tail must equal the no-restart tail.
    """
    task = {"id": "t1", "status": "pending"}
    spawns = 5
    restart_after = 2

    async def _spawn(orch: AgentOrchestrator) -> bool:
        fake_audit = AsyncMock()
        fake_audit.has_recent_tracing_gap = AsyncMock(return_value=False)
        with (
            patch("roboco.services.audit.get_audit_service", return_value=fake_audit),
            patch(
                "roboco.services.notification.NotificationService",
                return_value=AsyncMock(),
            ),
        ):
            return await orch._pm_respawn_should_gate("be-pm", task)

    no_restart = _new_orchestrator()
    cast("Any", no_restart)._schedule_respawn_persist = MagicMock()
    full = [await _spawn(no_restart) for _ in range(spawns)]

    pre = _new_orchestrator()
    cast("Any", pre)._schedule_respawn_persist = MagicMock()
    for _ in range(restart_after):
        await _spawn(pre)
    snapshot = copy.deepcopy(pre._pm_respawn_tracker)

    loaded = _new_orchestrator()
    cast("Any", loaded)._schedule_respawn_persist = MagicMock()
    loaded._pm_respawn_tracker = snapshot
    tail = [await _spawn(loaded) for _ in range(spawns - restart_after)]

    assert tail == full[restart_after:]
