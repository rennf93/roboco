"""Display-timezone day bucketing — pure, DB-free.

DB storage stays UTC canonical everywhere; these functions only decide which
calendar day (in the operator's configured ``display_timezone``) a UTC
instant belongs to, for read-side "today" / trailing-window aggregation
(currently the Telegram cockpit's Today brief + bot commands). No writes, no
ORM, no settings import — callers pass the configured tz name in.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def resolve_zone(tz_name: str) -> ZoneInfo:
    """``ZoneInfo`` for ``tz_name``, falling back to UTC on an unknown name.

    Settings validation already rejects a bad name at load time; this is a
    defensive fallback for a value that reached here some other way (e.g. a
    stale env read before validation ran) rather than a 500.
    """
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def local_date(instant: datetime, tz_name: str) -> date:
    """The calendar date ``instant`` (any tz-aware datetime) falls on in
    ``tz_name``."""
    return instant.astimezone(resolve_zone(tz_name)).date()


def trailing_dates(
    tz_name: str, days: int, *, now: datetime | None = None
) -> list[date]:
    """The last ``days`` calendar dates in ``tz_name``, oldest -> today
    (inclusive of today)."""
    today = local_date(now or datetime.now(UTC), tz_name)
    return [today - timedelta(days=n) for n in reversed(range(days))]


def day_bounds_utc(tz_name: str, day: date) -> tuple[datetime, datetime]:
    """UTC ``[start, end)`` instants spanning ``day``'s midnight-to-midnight
    window in ``tz_name`` — correct across a DST transition day (a spring-
    forward day is a real 23h UTC span, fall-back a real 25h one) because
    ``ZoneInfo`` re-resolves the offset from the wall-clock fields at
    ``.astimezone()`` time rather than freezing it at construction."""
    zone = resolve_zone(tz_name)
    start_local = datetime(day.year, day.month, day.day, tzinfo=zone)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)
