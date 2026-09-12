"""Fleet active-time clock derived from `dispatcher.alive` audit rows.

Every "stale / stuck / unattended / blocked too long / notification expired"
check in the platform computes `now - stored_timestamp`, so a CEO-ordered
stack pause or shutdown reads as neglect for the whole outage. The
orchestrator writes a `dispatcher.alive` audit row every
`AgentOrchestrator._DISPATCH_HEARTBEAT_SECONDS` (300s); its absence is a real
outage ("down"), and a row carrying `dispatch_paused=True` is a deliberate
maintenance pause ("paused") rather than a bug. `UptimeLedger` turns that row
stream into downtime windows so callers can compute active-only elapsed time
instead of raw wall-clock elapsed time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, ClassVar

import structlog
from sqlalchemy import select

from roboco.db.tables import AuditLogTable

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = structlog.get_logger()

# Mirrors AgentOrchestrator._DISPATCH_HEARTBEAT_SECONDS. Not imported from
# there: roboco.runtime.orchestrator is a ~19k-line module pulling in
# docker/fastapi at import time, too heavy to load just for one int. Keep in
# sync by hand; a shared constants module is the upgrade if they ever drift.
_HEARTBEAT_SECONDS = 300


# Mutable module state lives on one instance (attribute writes, not `global`
# reassignment) - see mark_process_running / clip_running below.
class _State:
    """`running_since`: set once by the running process; a `dispatcher.alive`
    gap AFTER this point is a broken audit write, not real downtime - only
    this process's own heartbeat can go missing here, and it knows it is
    alive. `last_clip_warning_at`: throttle for the warning `clip_running`
    logs when it clips a window - a broken write path fails on every
    `_refresh_uptime` tick (60s), so this keeps the warning to once per
    outage instead of once a minute.
    """

    running_since: datetime | None = None
    last_clip_warning_at: datetime | None = None


_state = _State()
_CLIP_WARNING_THROTTLE_SECONDS = 600


def mark_process_running(at: datetime | None = None) -> None:
    """Record that this process is running as of `at` (default: now).

    Idempotent and monotonic backward: repeat calls only move the marker
    earlier, never later, so the process's real start wins even if a later
    caller passes an earlier `at`.
    """
    at = _as_utc(at) if at is not None else datetime.now(UTC)
    if _state.running_since is None or at < _state.running_since:
        _state.running_since = at


def process_running_since() -> datetime | None:
    """When this process marked itself running, or None if it never has."""
    return _state.running_since


def _reset_process_marker() -> None:
    """Test-only: clear the running-since marker between test cases."""
    _state.running_since = None


def _as_utc(value: datetime) -> datetime:
    """Assume UTC for a naive datetime (SQLite tests, legacy rows)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


@dataclass(frozen=True)
class DowntimeWindow:
    """One stretch of fleet inactivity.

    `kind` is `"down"` (no heartbeat arrived) or `"paused"` (a heartbeat
    arrived but reported `dispatch_paused=True`).
    """

    start: datetime
    end: datetime
    kind: str


def _merge_same_kind(windows: list[DowntimeWindow]) -> list[DowntimeWindow]:
    """Coalesce overlapping/adjacent windows that share a `kind`."""
    if not windows:
        return []
    ordered = sorted(windows, key=lambda w: (w.kind, w.start))
    merged: list[DowntimeWindow] = [ordered[0]]
    for window in ordered[1:]:
        prev = merged[-1]
        if prev.kind == window.kind and window.start <= prev.end:
            merged[-1] = DowntimeWindow(
                prev.start, max(prev.end, window.end), prev.kind
            )
        else:
            merged.append(window)
    return sorted(merged, key=lambda w: w.start)


def _union_duration(windows: list[DowntimeWindow]) -> timedelta:
    """Total time covered, kind-agnostic union so overlaps aren't double counted."""
    if not windows:
        return timedelta()
    ordered = sorted(windows, key=lambda w: w.start)
    total = timedelta()
    cur_start, cur_end = ordered[0].start, ordered[0].end
    for window in ordered[1:]:
        if window.start <= cur_end:
            cur_end = max(cur_end, window.end)
        else:
            total += cur_end - cur_start
            cur_start, cur_end = window.start, window.end
    total += cur_end - cur_start
    return total


def _clip_to_range(
    windows: list[DowntimeWindow], start: datetime, end: datetime
) -> list[DowntimeWindow]:
    """Trim every window to `[start, end]`, dropping any that empty out."""
    clipped: list[DowntimeWindow] = []
    for window in windows:
        clipped_start = max(window.start, start)
        clipped_end = min(window.end, end)
        if clipped_start < clipped_end:
            clipped.append(DowntimeWindow(clipped_start, clipped_end, window.kind))
    return clipped


def _raw_windows(
    normalized: list[tuple[datetime, bool]], until: datetime, interval: timedelta
) -> list[DowntimeWindow]:
    """Gap and pause windows straight off consecutive rows, plus a trailing gap."""
    windows: list[DowntimeWindow] = []
    for i, (ts, paused) in enumerate(normalized):
        prev_ts = normalized[i - 1][0] if i > 0 else ts - interval
        if paused:
            windows.append(DowntimeWindow(max(prev_ts, ts - interval), ts, "paused"))
        if i > 0 and ts - prev_ts > 2 * interval:
            windows.append(DowntimeWindow(prev_ts + interval, ts, "down"))

    last_ts = normalized[-1][0]
    if until - last_ts > 2 * interval:
        windows.append(DowntimeWindow(last_ts + interval, until, "down"))
    return windows


def windows_from_heartbeats(
    rows: list[tuple[datetime, bool]],
    *,
    since: datetime,
    until: datetime,
    interval_seconds: int,
) -> list[DowntimeWindow]:
    """Derive downtime windows from ordered `(timestamp, dispatch_paused)` rows.

    `rows` is expected pre-filtered to `event_type == "dispatcher.alive"`,
    fetched from `since - 2*interval` (so a gap that started just before
    `since` is still visible) through `until`, ascending by timestamp. A gap
    over `2*interval` between consecutive rows is a "down" window; a row
    reporting `dispatch_paused=True` marks the interval since its previous
    row as a "paused" window. No rows at all is no evidence, never downtime:
    a fresh install, a test database, or a process with no audit history
    keeps plain wall-clock elapsed instead of muting every staleness check.
    """
    since = _as_utc(since)
    until = _as_utc(until)
    interval = timedelta(seconds=interval_seconds)
    normalized = [(_as_utc(ts), paused) for ts, paused in rows]

    if not normalized:
        return []

    windows = _raw_windows(normalized, until, interval)
    return _merge_same_kind(_clip_to_range(windows, since, until))


def clip_running(
    windows: list[DowntimeWindow], running_since: datetime | None
) -> list[DowntimeWindow]:
    """Clip `"down"` windows to end at `running_since`; drop ones entirely after it.

    This process knows it is running: a `dispatcher.alive` gap AFTER
    `running_since` is a broken audit write (the fire-and-forget insert
    failed), never a real outage - only a gap that predates `running_since`
    can be genuine downtime. `"paused"` windows are explicit flags off a
    real heartbeat row and are never touched.
    """
    if running_since is None:
        return windows
    running_since = _as_utc(running_since)

    # A clip shorter than one heartbeat interval is the normal boot seam
    # (the running mark lands a moment before the boot heartbeat row), not
    # a broken audit write, so it never warns.
    tolerance = timedelta(seconds=_HEARTBEAT_SECONDS)
    clipped: list[DowntimeWindow] = []
    changed: list[DowntimeWindow] = []
    for window in windows:
        if window.kind != "down":
            clipped.append(window)
            continue
        end = min(window.end, running_since)
        if window.end - end > tolerance:
            changed.append(window)
        if end > window.start:
            clipped.append(DowntimeWindow(window.start, end, window.kind))

    if changed:
        _warn_heartbeats_missing_while_running(changed, running_since)
    return clipped


def _warn_heartbeats_missing_while_running(
    changed: list[DowntimeWindow], running_since: datetime
) -> None:
    """Throttled warning: a real outage never triggers this, only a broken
    heartbeat write while the process is known to be alive."""
    now = datetime.now(UTC)
    last = _state.last_clip_warning_at
    if (
        last is not None
        and (now - last).total_seconds() < _CLIP_WARNING_THROTTLE_SECONDS
    ):
        return
    _state.last_clip_warning_at = now
    logger.warning(
        "dispatcher heartbeats missing while running; audit trail broken, not downtime",
        running_since=running_since.isoformat(),
        clipped_span_start=min(w.start for w in changed).isoformat(),
        clipped_span_end=max(w.end for w in changed).isoformat(),
    )


class UptimeLedger:
    """Fleet active-time clock derived from `dispatcher.alive` audit rows."""

    heartbeat_seconds: ClassVar[int] = _HEARTBEAT_SECONDS

    def __init__(self, windows: list[DowntimeWindow], loaded_at: datetime) -> None:
        self._windows = sorted(windows, key=lambda w: w.start)
        self.loaded_at = _as_utc(loaded_at)

    @classmethod
    async def load(
        cls,
        session: AsyncSession,
        *,
        since: datetime,
        until: datetime | None = None,
        running_since: datetime | None = None,
    ) -> UptimeLedger:
        """Fetch `dispatcher.alive` rows and build the ledger's downtime windows.

        `running_since` defaults to `process_running_since()`: this process
        knows it is alive, so a heartbeat gap after that point is a broken
        audit write, never downtime (see `clip_running`).
        """
        since = _as_utc(since)
        until = _as_utc(until) if until is not None else datetime.now(UTC)
        running_since = (
            running_since if running_since is not None else process_running_since()
        )
        interval = timedelta(seconds=cls.heartbeat_seconds)

        result = await session.execute(
            select(AuditLogTable.timestamp, AuditLogTable.details)
            .where(AuditLogTable.event_type == "dispatcher.alive")
            .where(AuditLogTable.timestamp >= since - 2 * interval)
            .where(AuditLogTable.timestamp <= until)
            .order_by(AuditLogTable.timestamp.asc())
        )
        rows = [
            (timestamp, bool((details or {}).get("dispatch_paused", False)))
            for timestamp, details in result.all()
        ]
        windows = windows_from_heartbeats(
            rows, since=since, until=until, interval_seconds=cls.heartbeat_seconds
        )
        windows = clip_running(windows, running_since)
        return cls(windows, until)

    def downtime_windows(
        self, start: datetime, end: datetime | None = None
    ) -> list[DowntimeWindow]:
        """Stored windows clipped to `[start, end]` (`end` defaults to load time)."""
        start = _as_utc(start)
        end = _as_utc(end) if end is not None else self.loaded_at
        return _merge_same_kind(_clip_to_range(self._windows, start, end))

    def downtime(self, start: datetime, end: datetime | None = None) -> timedelta:
        """Total downtime in `[start, end]`, union of windows, no double counting."""
        return _union_duration(self.downtime_windows(start, end))

    def active_elapsed(self, start: datetime, end: datetime | None = None) -> timedelta:
        """Wall-clock elapsed in `[start, end]` minus downtime, never negative."""
        start = _as_utc(start)
        end = _as_utc(end) if end is not None else self.loaded_at
        if end <= start:
            return timedelta()
        active = (end - start) - self.downtime(start, end)
        return active if active > timedelta() else timedelta()

    def active_seconds(self, start: datetime, end: datetime | None = None) -> float:
        """`active_elapsed` as a float second count."""
        return self.active_elapsed(start, end).total_seconds()
