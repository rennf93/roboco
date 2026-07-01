"""Per-stage active-vs-wait decomposition — pure overlap math (no DB).

A task's wall-clock lifetime is a sequence of status windows (from the audit
log). During each window some agent spawn *stints* may have been running. This
splits each window into ``active`` (wall-clock during which at least one stint
was running) and ``wait`` (the remainder — queue/review idle). Overlapping
stints are merged, so ``active`` can never exceed the window length and
``active + wait == window length``.

This is the wall-clock decomposition and is deliberately distinct from *summed
effort* (Σ stint durations), which can exceed wall-clock when stints run
concurrently and is computed separately at the service layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime

# A status window: (status, entered_at, exited_at). An open final window passes
# ``now()`` as exited_at at the call site.
StageWindow = tuple[str, "datetime", "datetime"]
# A spawn stint: (started_at, ended_at). An in-flight open stint passes
# ``now()`` as ended_at at the call site.
Stint = tuple["datetime", "datetime"]


@dataclass(frozen=True)
class StageEffort:
    """Active vs wait seconds for one status window."""

    status: str
    active_seconds: int
    wait_seconds: int

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "active_seconds": self.active_seconds,
            "wait_seconds": self.wait_seconds,
        }


def _merged_overlap_seconds(
    win_start: datetime, win_end: datetime, stints: Sequence[Stint]
) -> float:
    """Wall-clock seconds inside [win_start, win_end) covered by any stint.

    Stints are clipped to the window and overlapping ones merged, so concurrent
    stints are counted once (never exceeds the window length).
    """
    clipped: list[tuple[datetime, datetime]] = []
    for stint_start, stint_end in stints:
        lo = max(win_start, stint_start)
        hi = min(win_end, stint_end)
        if hi > lo:
            clipped.append((lo, hi))
    if not clipped:
        return 0.0
    clipped.sort()
    total = 0.0
    cur_lo, cur_hi = clipped[0]
    for lo, hi in clipped[1:]:
        if lo <= cur_hi:  # overlapping/adjacent — extend the current run
            cur_hi = max(cur_hi, hi)
        else:
            total += (cur_hi - cur_lo).total_seconds()
            cur_lo, cur_hi = lo, hi
    total += (cur_hi - cur_lo).total_seconds()
    return total


def compute_stage_effort(
    windows: Sequence[StageWindow], stints: Sequence[Stint]
) -> list[StageEffort]:
    """Decompose each status window into active vs wait seconds.

    ``active`` = merged wall-clock overlap of the stints with the window;
    ``wait`` = window length minus active (clamped >= 0). A zero/negative-length
    window yields ``(0, 0)``. Seconds are rounded to whole ints for a stable
    API surface.
    """
    result: list[StageEffort] = []
    for status, start, end in windows:
        span = (end - start).total_seconds()
        if span <= 0:
            result.append(StageEffort(status=status, active_seconds=0, wait_seconds=0))
            continue
        active = _merged_overlap_seconds(start, end, stints)
        active = min(active, span)
        wait = max(0.0, span - active)
        result.append(
            StageEffort(
                status=status,
                active_seconds=round(active),
                wait_seconds=round(wait),
            )
        )
    return result
