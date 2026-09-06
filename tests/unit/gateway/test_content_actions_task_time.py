"""Tests for ContentActions.task_time: uptime-adjusted elapsed times.

Mirrors read_a2a's test shape (see test_content_actions.py). The pure
seconds-math helper is tested without a DB; the action is driven with the
usual MagicMock task-service doubles and a monkeypatched UptimeLedger.load.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from roboco.services.gateway.content_actions import (
    ContentActions,
    ContentActionsDeps,
    _task_time_seconds,
)
from roboco.services.uptime import DowntimeWindow, UptimeLedger

_T0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)

# ---------------------------------------------------------------------------
# _task_time_seconds: pure helper
# ---------------------------------------------------------------------------


def test_null_timestamp_maps_to_null_seconds_both_dicts() -> None:
    ledger = UptimeLedger([], _T0)
    wall, active = _task_time_seconds({"age": None}, now=_T0, ledger=ledger)
    assert wall == {"age": None}
    assert active == {"age": None}


def test_active_seconds_subtracts_downtime_wall_does_not() -> None:
    started = _T0
    now = _T0 + timedelta(hours=2)
    down_window = DowntimeWindow(
        _T0 + timedelta(minutes=30), _T0 + timedelta(minutes=45), "down"
    )
    ledger = UptimeLedger([down_window], now)

    wall, active = _task_time_seconds({"age": started}, now=now, ledger=ledger)

    assert wall["age"] == timedelta(hours=2).total_seconds()
    assert active["age"] == (timedelta(hours=2) - timedelta(minutes=15)).total_seconds()


def test_naive_timestamp_is_treated_as_utc() -> None:
    """SQLite-test-style naive datetimes must not crash the subtraction."""
    naive_start = datetime(2026, 1, 1, 0, 0, 0)  # no tzinfo
    now = _T0 + timedelta(hours=1)
    ledger = UptimeLedger([], now)

    wall, active = _task_time_seconds({"age": naive_start}, now=now, ledger=ledger)

    assert wall["age"] == timedelta(hours=1).total_seconds()
    assert active["age"] == timedelta(hours=1).total_seconds()


# ---------------------------------------------------------------------------
# task_time action
# ---------------------------------------------------------------------------


def _make_deps(task: AsyncMock) -> ContentActionsDeps:
    return ContentActionsDeps(
        task=task,
        git=AsyncMock(),
        a2a=AsyncMock(),
        journal=AsyncMock(),
        workspace=AsyncMock(),
        notifications=AsyncMock(),
    )


def _task(task_id: UUID, **fields: Any) -> MagicMock:
    """A task double with the timestamp/status fields task_time reads.

    Unset fields default to None (or "in_progress" for status).
    """
    defaults: dict[str, Any] = {
        "status": "in_progress",
        "parent_task_id": None,
        "created_at": _T0,
        "claimed_at": None,
        "updated_at": None,
        "last_heartbeat_at": None,
        "stalled_since": None,
    }
    defaults.update(fields)
    return MagicMock(id=task_id, **defaults)


@pytest.fixture(autouse=True)
def _fake_ledger(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every task_time call gets an empty-downtime ledger unless overridden."""
    empty_ledger = UptimeLedger([], _T0 + timedelta(days=1))
    monkeypatch.setattr(UptimeLedger, "load", AsyncMock(return_value=empty_ledger))


@pytest.mark.asyncio
async def test_task_time_not_found_when_task_missing() -> None:
    task_svc = AsyncMock()
    task_svc.get.return_value = None
    ca = ContentActions(_make_deps(task_svc))

    env = await ca.task_time(agent_id=uuid4(), task_id=uuid4())
    body = env.as_dict()

    assert body["error"] == "not_found"


@pytest.mark.asyncio
async def test_task_time_walks_to_root_over_three_levels() -> None:
    root_id, mid_id, leaf_id = uuid4(), uuid4(), uuid4()
    root = _task(root_id, created_at=_T0)
    mid = _task(mid_id, parent_task_id=root_id, created_at=_T0 + timedelta(hours=1))
    leaf = _task(leaf_id, parent_task_id=mid_id, created_at=_T0 + timedelta(hours=2))

    by_id = {root_id: root, mid_id: mid, leaf_id: leaf}
    task_svc = AsyncMock()
    task_svc.get = AsyncMock(side_effect=by_id.get)
    ca = ContentActions(_make_deps(task_svc))

    env = await ca.task_time(agent_id=uuid4(), task_id=leaf_id)
    body = env.as_dict()

    assert body["error"] is None
    assert body["evidence"]["root_task_id"] == str(root_id)
    assert body["evidence"]["root_created_at"] == root.created_at.isoformat()


@pytest.mark.asyncio
async def test_task_time_root_defaults_to_self_when_no_parent() -> None:
    task_id = uuid4()
    task_svc = AsyncMock()
    task_svc.get.return_value = _task(task_id, created_at=_T0)
    ca = ContentActions(_make_deps(task_svc))

    env = await ca.task_time(agent_id=uuid4(), task_id=task_id)
    body = env.as_dict()

    assert body["evidence"]["root_task_id"] == str(task_id)


@pytest.mark.asyncio
async def test_task_time_envelope_shape() -> None:
    task_id = uuid4()
    claimed = _T0 + timedelta(minutes=10)
    task_svc = AsyncMock()
    task_svc.get.return_value = _task(
        task_id,
        created_at=_T0,
        claimed_at=claimed,
        updated_at=claimed,
        last_heartbeat_at=claimed,
        stalled_since=None,
    )
    ca = ContentActions(_make_deps(task_svc))

    env = await ca.task_time(agent_id=uuid4(), task_id=task_id)
    body = env.as_dict()

    assert body["status"] == "measured"
    assert body["task_id"] == str(task_id)
    assert "active_seconds" in body["next"] or "active_seconds" in str(body["next"])
    evidence = body["evidence"]
    for key in (
        "task_id",
        "status",
        "root_task_id",
        "root_created_at",
        "created_at",
        "claimed_at",
        "updated_at",
        "last_heartbeat_at",
        "stalled_since",
        "wall_seconds",
        "active_seconds",
        "fleet_downtime_seconds",
        "downtime_windows",
    ):
        assert key in evidence, key
    for key in (
        "age",
        "root_age",
        "in_state",
        "since_claim",
        "since_heartbeat",
        "stalled",
    ):
        assert key in evidence["wall_seconds"], key
        assert key in evidence["active_seconds"], key
    assert evidence["stalled_since"] is None
    assert evidence["wall_seconds"]["stalled"] is None
    assert evidence["active_seconds"]["stalled"] is None
    assert evidence["wall_seconds"]["since_claim"] is not None
    task_svc.get.assert_awaited()
