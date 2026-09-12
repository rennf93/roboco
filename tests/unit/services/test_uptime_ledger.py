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
from roboco.services import uptime as uptime_module
from roboco.services.uptime import (
    DowntimeWindow,
    UptimeLedger,
    clip_running,
    mark_process_running,
    windows_from_heartbeats,
)

_INTERVAL = 300  # seconds, mirrors AgentOrchestrator._DISPATCH_HEARTBEAT_SECONDS
_T0 = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
_TWO_WINDOWS = 2
_ONE_HOUR_SECONDS = 3600.0


@pytest.fixture(autouse=True)
def _reset_running_marker() -> Any:
    """Isolate the running-since marker and the clip-warning throttle between
    tests - both live on `uptime._state` and are mutated by
    `mark_process_running` / `clip_running`."""
    uptime_module._reset_process_marker()
    uptime_module._state.last_clip_warning_at = None
    yield
    uptime_module._reset_process_marker()
    uptime_module._state.last_clip_warning_at = None


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


def test_no_rows_yields_no_windows_even_over_a_long_range() -> None:
    """No heartbeat history is no evidence of downtime: wall clock applies."""
    since = _T0
    until = _T0 + timedelta(days=7)

    windows = windows_from_heartbeats(
        [], since=since, until=until, interval_seconds=_INTERVAL
    )

    assert windows == []


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


# ---------------------------------------------------------------------------
# clip_running: this process knows it is alive, so a heartbeat gap AFTER it
# started running is a broken audit write, never real downtime.
# ---------------------------------------------------------------------------


def test_clip_running_none_running_since_is_a_no_op() -> None:
    window = DowntimeWindow(_T0, _T0 + timedelta(hours=1), "down")

    assert clip_running([window], None) == [window]


def test_clip_running_drops_a_trailing_down_window_after_running_since() -> None:
    running_since = _T0 + timedelta(hours=1)
    window = DowntimeWindow(
        running_since + timedelta(minutes=5),
        running_since + timedelta(minutes=20),
        "down",
    )

    assert clip_running([window], running_since) == []


def test_clip_running_clips_a_window_straddling_running_since() -> None:
    running_since = _T0 + timedelta(hours=1)
    window = DowntimeWindow(
        running_since - timedelta(minutes=10),
        running_since + timedelta(minutes=10),
        "down",
    )

    assert clip_running([window], running_since) == [
        DowntimeWindow(window.start, running_since, "down")
    ]


def test_clip_running_boot_seam_clip_does_not_warn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The running mark lands a moment before the boot heartbeat row, so the
    pre-boot outage window overshoots the mark by seconds: clipped, no
    warning (that is the normal boot seam, not a broken audit write)."""
    running_since = _T0 + timedelta(hours=1)
    window = DowntimeWindow(
        running_since - timedelta(minutes=10),
        running_since + timedelta(seconds=5),
        "down",
    )
    logged: list[Any] = []
    monkeypatch.setattr(
        uptime_module.logger, "warning", lambda *a, **k: logged.append((a, k))
    )

    assert clip_running([window], running_since) == [
        DowntimeWindow(window.start, running_since, "down")
    ]
    assert logged == []


def test_clip_running_leaves_paused_and_pre_boot_down_windows_alone() -> None:
    running_since = _T0 + timedelta(hours=1)
    paused = DowntimeWindow(
        running_since + timedelta(minutes=5),
        running_since + timedelta(minutes=20),
        "paused",
    )
    pre_boot_down = DowntimeWindow(_T0, running_since - timedelta(minutes=30), "down")

    assert clip_running([paused, pre_boot_down], running_since) == [
        paused,
        pre_boot_down,
    ]


def test_clip_running_warning_throttled_within_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    running_since = _T0 + timedelta(hours=1)
    window = DowntimeWindow(
        running_since - timedelta(minutes=10),
        running_since + timedelta(minutes=10),
        "down",
    )
    logged: list[Any] = []
    monkeypatch.setattr(
        uptime_module.logger, "warning", lambda *a, **k: logged.append((a, k))
    )

    clip_running([window], running_since)
    clip_running([window], running_since)

    assert len(logged) == 1


def test_clip_running_warning_fires_again_after_throttle_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    running_since = _T0 + timedelta(hours=1)
    window = DowntimeWindow(
        running_since - timedelta(minutes=10),
        running_since + timedelta(minutes=10),
        "down",
    )
    logged: list[Any] = []
    monkeypatch.setattr(
        uptime_module.logger, "warning", lambda *a, **k: logged.append((a, k))
    )

    clip_running([window], running_since)
    monkeypatch.setattr(
        uptime_module._state,
        "last_clip_warning_at",
        datetime.now(UTC)
        - timedelta(seconds=uptime_module._CLIP_WARNING_THROTTLE_SECONDS + 1),
    )
    clip_running([window], running_since)

    assert len(logged) == _TWO_WINDOWS


# ---------------------------------------------------------------------------
# UptimeLedger.load + running_since: a broken audit write while this process
# is known to be running must never collapse active_elapsed to ~0.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_heartbeats_stop_with_running_since_clips_the_post_boot_gap() -> (
    None
):
    """One heartbeat at the start, then silence, but the process marked
    itself running partway through the range: the post-boot part is a
    broken audit write (no windows), the pre-boot gap is still downtime."""
    since = _T0
    running_since = _T0 + timedelta(hours=5)
    until = running_since + timedelta(hours=1)
    session, _recorder = _fake_session([(since, {})])

    ledger = await UptimeLedger.load(
        session, since=since, until=until, running_since=running_since
    )

    assert ledger.downtime_windows(since, running_since) == [
        DowntimeWindow(since + timedelta(seconds=_INTERVAL), running_since, "down")
    ]
    assert ledger.downtime_windows(running_since, until) == []
    assert ledger.active_elapsed(running_since, until) == timedelta(hours=1)


@pytest.mark.asyncio
async def test_load_picks_up_process_running_since_by_default() -> None:
    since = _T0
    running_since = _T0 + timedelta(hours=5)
    until = running_since + timedelta(hours=1)
    mark_process_running(running_since)
    session, _recorder = _fake_session([(since, {})])

    ledger = await UptimeLedger.load(session, since=since, until=until)

    assert ledger.downtime_windows(running_since, until) == []
