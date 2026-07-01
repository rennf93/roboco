"""compute_stage_effort — split each stage window into active vs wait seconds.

Pure overlap math (no DB): given a stage's [start, end) window and the agent
spawn stints that ran during the task, ``active`` is the wall-clock time during
which AT LEAST ONE stint was running (overlapping stints merged, so active can
never exceed the window), and ``wait`` is the remainder. This is the wall-clock
decomposition — distinct from summed effort (Σ stint durations), which can
exceed wall-clock when stints run concurrently.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from roboco.foundation.policy.stage_effort import StageEffort, compute_stage_effort

_BASE = datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC)


def _at(seconds: int) -> datetime:
    return _BASE + timedelta(seconds=seconds)


def _window(status: str, start_s: int, end_s: int) -> tuple[str, datetime, datetime]:
    return (status, _at(start_s), _at(end_s))


def _stint(start_s: int, end_s: int) -> tuple[datetime, datetime]:
    return (_at(start_s), _at(end_s))


def _only(windows: list, stints: list) -> StageEffort:
    result = compute_stage_effort(windows, stints)
    assert len(result) == 1
    return result[0]


def test_disjoint_stint_is_all_wait() -> None:
    eff = _only([_window("in_progress", 0, 100)], [_stint(200, 300)])
    assert (eff.active_seconds, eff.wait_seconds) == (0, 100)


def test_fully_nested_stint() -> None:
    eff = _only([_window("in_progress", 0, 100)], [_stint(20, 50)])
    assert (eff.active_seconds, eff.wait_seconds) == (30, 70)


def test_partial_overlap_clips_to_window() -> None:
    # stint runs 80..150 but window ends at 100 -> only 20s active in-window.
    eff = _only([_window("in_progress", 0, 100)], [_stint(80, 150)])
    assert (eff.active_seconds, eff.wait_seconds) == (20, 80)


def test_multiple_nonoverlapping_stints_sum() -> None:
    eff = _only(
        [_window("in_progress", 0, 100)],
        [_stint(0, 10), _stint(40, 60)],
    )
    assert (eff.active_seconds, eff.wait_seconds) == (30, 70)


def test_overlapping_stints_are_merged_not_double_counted() -> None:
    # [10,40) and [30,60) overlap -> merged union is [10,60) = 50s, NOT 60s.
    eff = _only(
        [_window("in_progress", 0, 100)],
        [_stint(10, 40), _stint(30, 60)],
    )
    # merged union [10,60) = 50s active, 50s wait (NOT 60s from double-count).
    assert (eff.active_seconds, eff.wait_seconds) == (50, 50)


def test_active_never_exceeds_window_length() -> None:
    eff = _only(
        [_window("in_progress", 0, 100)],
        [_stint(-50, 500)],  # stint dwarfs the window
    )
    assert (eff.active_seconds, eff.wait_seconds) == (100, 0)


def test_zero_length_window() -> None:
    eff = _only([_window("claimed", 50, 50)], [_stint(0, 100)])
    assert (eff.active_seconds, eff.wait_seconds) == (0, 0)


def test_each_window_decomposes_independently() -> None:
    windows = [_window("claimed", 0, 100), _window("in_progress", 100, 300)]
    stints = [_stint(50, 250)]  # spans both windows
    result = compute_stage_effort(windows, stints)
    by_status = {e.status: e for e in result}
    claimed = by_status["claimed"]
    in_progress = by_status["in_progress"]
    assert (claimed.active_seconds, claimed.wait_seconds) == (50, 50)
    # in_progress: stint covers 100..250 of the 100..300 window.
    assert (in_progress.active_seconds, in_progress.wait_seconds) == (150, 50)


def test_to_dict_shape() -> None:
    eff = _only([_window("in_progress", 0, 100)], [_stint(20, 50)])
    d = eff.to_dict()
    assert d == {"status": "in_progress", "active_seconds": 30, "wait_seconds": 70}
