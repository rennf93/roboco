"""display_time — pure display-timezone day bucketing, incl. DST boundaries."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from roboco.foundation.policy.display_time import (
    day_bounds_utc,
    local_date,
    resolve_zone,
    trailing_dates,
)


class TestResolveZone:
    def test_known_zone(self) -> None:
        assert resolve_zone("Europe/Berlin").key == "Europe/Berlin"

    def test_utc_default(self) -> None:
        assert resolve_zone("UTC").key == "UTC"

    def test_unknown_zone_falls_back_to_utc(self) -> None:
        assert resolve_zone("Not/AZone").key == "UTC"


class TestLocalDate:
    def test_utc_noop(self) -> None:
        instant = datetime(2026, 7, 23, 10, 0, tzinfo=UTC)
        assert local_date(instant, "UTC") == date(2026, 7, 23)

    def test_gmt_plus_2_evening_utc_is_next_day_local(self) -> None:
        """22:30 UTC on the 22nd is 00:30 the NEXT day in GMT+2 — the exact
        'CEO's evening activity lands on the wrong display day' bug."""
        instant = datetime(2026, 7, 22, 22, 30, tzinfo=UTC)
        assert local_date(instant, "Europe/Berlin") == date(2026, 7, 23)

    def test_gmt_plus_2_early_morning_utc_is_prior_day_local(self) -> None:
        # No — GMT+2 is AHEAD of UTC, so early UTC morning is still the same
        # local day; use a clearly-behind zone for the "prior day" case.
        instant = datetime(2026, 7, 23, 2, 0, tzinfo=UTC)
        assert local_date(instant, "America/Los_Angeles") == date(2026, 7, 22)


class TestTrailingDates:
    def test_seven_days_oldest_to_today(self) -> None:
        now = datetime(2026, 7, 23, 12, 0, tzinfo=UTC)
        dates = trailing_dates("UTC", 7, now=now)
        assert len(dates) == 7  # noqa: PLR2004
        assert dates[-1] == date(2026, 7, 23)
        assert dates[0] == date(2026, 7, 17)
        assert dates == sorted(dates)

    def test_timezone_shifts_which_day_is_today(self) -> None:
        """23:00 UTC on the 22nd is already the 23rd in Europe/Berlin."""
        now = datetime(2026, 7, 22, 23, 0, tzinfo=UTC)
        assert trailing_dates("UTC", 1, now=now) == [date(2026, 7, 22)]
        assert trailing_dates("Europe/Berlin", 1, now=now) == [date(2026, 7, 23)]


class TestDayBoundsUtc:
    def test_utc_day_is_exactly_24h(self) -> None:
        start, end = day_bounds_utc("UTC", date(2026, 7, 23))
        assert start == datetime(2026, 7, 23, 0, 0, tzinfo=UTC)
        assert end == datetime(2026, 7, 24, 0, 0, tzinfo=UTC)
        assert (end - start).total_seconds() == 24 * 3600

    def test_gmt_plus_2_day_bounds(self) -> None:
        start, end = day_bounds_utc("Europe/Berlin", date(2026, 7, 23))
        # Summer time (CEST, UTC+2): local midnight is 22:00 UTC the day before.
        assert start == datetime(2026, 7, 22, 22, 0, tzinfo=UTC)
        assert end == datetime(2026, 7, 23, 22, 0, tzinfo=UTC)

    def test_dst_spring_forward_day_is_23_hours(self) -> None:
        """Europe/Berlin springs forward on the last Sunday of March —
        2026-03-29 02:00 CET -> 03:00 CEST — so that local day is only 23h
        of real UTC time, not 24."""
        start, end = day_bounds_utc("Europe/Berlin", date(2026, 3, 29))
        assert (end - start).total_seconds() == 23 * 3600

    def test_dst_fall_back_day_is_25_hours(self) -> None:
        """Europe/Berlin falls back on the last Sunday of October —
        2026-10-25 03:00 CEST -> 02:00 CET — so that local day is 25h."""
        start, end = day_bounds_utc("Europe/Berlin", date(2026, 10, 25))
        assert (end - start).total_seconds() == 25 * 3600

    def test_an_instant_at_start_falls_in_this_day(self) -> None:
        start, _ = day_bounds_utc("Europe/Berlin", date(2026, 7, 23))
        assert local_date(start, "Europe/Berlin") == date(2026, 7, 23)

    def test_an_instant_just_before_end_falls_in_this_day(self) -> None:
        _, end = day_bounds_utc("Europe/Berlin", date(2026, 7, 23))
        assert local_date(end - timedelta(seconds=1), "Europe/Berlin") == date(
            2026, 7, 23
        )
