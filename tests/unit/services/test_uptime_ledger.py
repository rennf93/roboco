"""UptimeLedger: a maintenance pause or a real outage must not read as neglect.

Every "stale / stuck / unattended / blocked too long / notification expired"
check computes `now - stored_timestamp`, so a CEO-ordered stack pause or a
genuine dead-loop outage both inflate that delta identically. `dispatcher.
alive` audit rows (roboco.runtime.orchestrator._emit_dispatcher_heartbeat)
are the durable liveness signal; `windows_from_heartbeats` turns a row stream
into downtime windows, and `UptimeLedger` sums them into an active-only clock.
These tests are pure (no DB) except the `load` tests, which fake the session.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from roboco.services.uptime import DowntimeWindow, UptimeLedger, windows_from_heartbeats

_INTERVAL = 300  # seconds, mirrors AgentOrchestrator._DISPATCH_HEARTBEAT_SECONDS
_T0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
_TWO_WINDOWS = 2
_ONE_HOUR_SECONDS = 3600.0


def _rows(*offsets_and_paused: tuple[int, bool]) -> list[tuple[datetime, bool]]:
    """Rows at `_T0 + offset` seconds, ascending, as `(timestamp, paused)`."""
    return [
        (_T0 + timedelta(seconds=offset), paused)
        for offset, paused in offsets_and_paused
    ]


# ---------------------------------------------------------------------------
# windows_from_heartbeats
# ---------------------------------------------------------------------------


def test_steady_heartbeats_yield_no_windows() -> None:
    rows = _rows(*[(i * _INTERVAL, False) for i in range(13)])  # 1 hour, on the dot
    since, until = rows[0][0], rows[-1][0]

    windows = windows_from_heartbeats(
        rows, since=since, until=until, interval_seconds=_INTERVAL
    )

    assert windows == []


def test_steady_heartbeats_active_elapsed_equals_wall_elapsed() -> None:
    rows = _rows(*[(i * _INTERVAL, False) for i in range(13)])
    since, until = rows[0][0], rows[-1][0]
    windows = windows_from_heartbeats(
        rows, since=since, until=until, interval_seconds=_INTERVAL
    )
    ledger = UptimeLedger(windows, until)

    assert ledger.active_elapsed(since, until) == until - since


def test_six_hour_gap_yields_one_down_window_minus_one_interval() -> None:
    rows = _rows((0, False), (6 * 3600, False))
    since, until = rows[0][0], rows[-1][0]

    windows = windows_from_heartbeats(
        rows, since=since, until=until, interval_seconds=_INTERVAL
    )

    assert windows == [
        DowntimeWindow(since + timedelta(seconds=_INTERVAL), until, "down")
    ]


def test_six_hour_gap_active_elapsed_subtracts_the_window() -> None:
    rows = _rows((0, False), (6 * 3600, False))
    since, until = rows[0][0], rows[-1][0]
    windows = windows_from_heartbeats(
        rows, since=since, until=until, interval_seconds=_INTERVAL
    )
    ledger = UptimeLedger(windows, until)

    # Alive up to one interval past the last good heartbeat before the gap.
    assert ledger.active_elapsed(since, until) == timedelta(seconds=_INTERVAL)


def test_downtime_windows_clipped_to_a_sub_range() -> None:
    rows = _rows((0, False), (6 * 3600, False))
    since, until = rows[0][0], rows[-1][0]
    windows = windows_from_heartbeats(
        rows, since=since, until=until, interval_seconds=_INTERVAL
    )
    ledger = UptimeLedger(windows, until)

    sub_start = since + timedelta(hours=1)
    sub_end = since + timedelta(hours=2)
    clipped = ledger.downtime_windows(sub_start, sub_end)

    assert clipped == [DowntimeWindow(sub_start, sub_end, "down")]


def test_consecutive_paused_heartbeats_coalesce_into_one_window() -> None:
    rows = _rows(
        (0, False),
        (300, True),
        (600, True),
        (900, True),
        (1200, False),
    )
    since, until = rows[0][0], rows[-1][0]

    windows = windows_from_heartbeats(
        rows, since=since, until=until, interval_seconds=_INTERVAL
    )

    assert windows == [
        DowntimeWindow(
            _T0 + timedelta(seconds=0), _T0 + timedelta(seconds=900), "paused"
        )
    ]
    assert windows[0].end - windows[0].start == timedelta(minutes=15)


def test_paused_window_excluded_from_active_elapsed() -> None:
    rows = _rows(
        (0, False),
        (300, True),
        (600, True),
        (900, True),
        (1200, False),
    )
    since, until = rows[0][0], rows[-1][0]
    windows = windows_from_heartbeats(
        rows, since=since, until=until, interval_seconds=_INTERVAL
    )
    ledger = UptimeLedger(windows, until)

    assert ledger.active_elapsed(since, until) == timedelta(seconds=1200 - 900)


def test_overlapping_paused_and_down_windows_union_not_double_counted() -> None:
    # Gap of 20 minutes (> 2*interval) with the resuming row itself paused:
    # down=[+5m,+20m), paused=[+15m,+20m): they overlap in [+15m,+20m).
    rows = _rows((0, False), (20 * 60, True))
    since, until = rows[0][0], rows[-1][0]

    windows = windows_from_heartbeats(
        rows, since=since, until=until, interval_seconds=_INTERVAL
    )
    ledger = UptimeLedger(windows, until)

    kinds = {w.kind for w in windows}
    assert kinds == {"down", "paused"}
    assert ledger.downtime(since, until) == timedelta(minutes=15)


def test_two_disjoint_down_windows_sum_without_merging() -> None:
    rows = _rows(
        (0, False),
        (6 * 3600, False),
        (6 * 3600 + 300, False),  # one live tick between the two outages
        (12 * 3600 + 300, False),
    )
    since, until = rows[0][0], rows[-1][0]

    windows = windows_from_heartbeats(
        rows, since=since, until=until, interval_seconds=_INTERVAL
    )
    ledger = UptimeLedger(windows, until)

    assert (
        len(windows) == _TWO_WINDOWS
    )  # not coalesced across the live gap between them
    assert ledger.downtime(since, until) == 2 * timedelta(hours=6, seconds=-_INTERVAL)


def test_trailing_dead_loop_yields_trailing_down_window() -> None:
    rows = _rows((0, False))
    since = rows[0][0]
    until = since + timedelta(minutes=20)

    windows = windows_from_heartbeats(
        rows, since=since, until=until, interval_seconds=_INTERVAL
    )

    assert windows == [
        DowntimeWindow(since + timedelta(seconds=_INTERVAL), until, "down")
    ]


def test_no_rows_short_range_yields_no_windows() -> None:
    since = _T0
    until = _T0 + timedelta(minutes=2)  # under 2*interval (10 min)

    windows = windows_from_heartbeats(
        [], since=since, until=until, interval_seconds=_INTERVAL
    )

    assert windows == []


def test_no_rows_long_range_yields_whole_range_down() -> None:
    since = _T0
    until = _T0 + timedelta(hours=1)

    windows = windows_from_heartbeats(
        [], since=since, until=until, interval_seconds=_INTERVAL
    )

    assert windows == [DowntimeWindow(since, until, "down")]


def test_naive_datetimes_are_accepted_as_utc() -> None:
    naive_t0 = _T0.replace(tzinfo=None)
    rows = [(naive_t0, False), (naive_t0 + timedelta(hours=6), False)]
    since = naive_t0
    until = naive_t0 + timedelta(hours=6)

    windows = windows_from_heartbeats(
        rows, since=since, until=until, interval_seconds=_INTERVAL
    )

    assert windows == [
        DowntimeWindow(
            _T0 + timedelta(seconds=_INTERVAL), _T0 + timedelta(hours=6), "down"
        )
    ]


# ---------------------------------------------------------------------------
# UptimeLedger arithmetic edge cases
# ---------------------------------------------------------------------------


def test_active_elapsed_never_negative_when_start_after_end() -> None:
    ledger = UptimeLedger([], _T0)

    assert ledger.active_elapsed(_T0 + timedelta(hours=1), _T0) == timedelta()


def test_active_seconds_matches_active_elapsed() -> None:
    ledger = UptimeLedger([], _T0 + timedelta(hours=1))

    assert ledger.active_seconds(_T0, _T0 + timedelta(hours=1)) == _ONE_HOUR_SECONDS


# ---------------------------------------------------------------------------
# UptimeLedger.load
# ---------------------------------------------------------------------------


def _fake_session(
    rows: list[tuple[datetime, dict[str, Any]]],
) -> tuple[MagicMock, list[Any]]:
    """A session whose execute() returns `rows` and records the statement sent."""
    recorder: list[Any] = []

    async def _execute(stmt: Any) -> MagicMock:
        recorder.append(stmt)
        result = MagicMock()
        result.all.return_value = rows
        return result

    session = MagicMock()
    session.execute = AsyncMock(side_effect=_execute)
    return session, recorder


def _compiled_sql(stmt: Any) -> str:
    return str(stmt.compile(compile_kwargs={"literal_binds": True}))


@pytest.mark.asyncio
async def test_load_filters_on_event_type_and_since_window() -> None:
    since = _T0 + timedelta(hours=1)
    until = _T0 + timedelta(hours=2)
    session, recorder = _fake_session([])

    await UptimeLedger.load(session, since=since, until=until)

    sql = _compiled_sql(recorder[0])
    assert "audit_log.event_type = 'dispatcher.alive'" in sql
    fetch_since = since - 2 * timedelta(seconds=_INTERVAL)
    assert f"audit_log.timestamp >= '{fetch_since}'" in sql
    assert f"audit_log.timestamp <= '{until}'" in sql


@pytest.mark.asyncio
async def test_load_missing_dispatch_paused_flag_counts_as_active() -> None:
    since = _T0
    until = _T0 + timedelta(seconds=_INTERVAL)
    rows = [(since, {}), (until, {"interval_seconds": _INTERVAL})]
    session, _recorder = _fake_session(rows)

    ledger = await UptimeLedger.load(session, since=since, until=until)

    assert ledger.downtime_windows(since, until) == []
    assert ledger.active_elapsed(since, until) == until - since


@pytest.mark.asyncio
async def test_load_reads_dispatch_paused_flag_from_details() -> None:
    since = _T0
    until = _T0 + timedelta(seconds=_INTERVAL)
    rows = [(since, {"dispatch_paused": False}), (until, {"dispatch_paused": True})]
    session, _recorder = _fake_session(rows)

    ledger = await UptimeLedger.load(session, since=since, until=until)

    assert ledger.downtime(since, until) == timedelta(seconds=_INTERVAL)
