"""Cross-task outage detection for the review path (evidence + git reads).

Extends the gateway-health recovery subsystem: the orchestrator's reaper tick
calls ``sweep_review_path_health`` beside ``_maybe_recover_broken_gateway``.
Failures of the evidence / git-readonly pipeline are recorded per task key; an
outage is declared only when failures span at least
``review_path_outage_distinct_tasks`` DISTINCT tasks inside the rolling
``review_path_outage_window_seconds`` window, so one task failing repeatedly is
never an outage. One notification is emitted per outage window (on trip) and
one on recovery. Detection only - no auto-remediation.

ponytail: in-memory per orchestrator process (evidence and the git routes run
in it); a restart forgets the window, which at worst delays a trip by one window.
"""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Any

from roboco.config import settings
from roboco.logging import get_logger

logger = get_logger(__name__)

OUTAGE_TYPE = "review_path_tools"


class ReviewPathHealthTracker:
    """Rolling-window failure tracker with a deduplicated outage state."""

    def __init__(self) -> None:
        self._failures: deque[tuple[datetime, str]] = deque()
        self._consecutive_ok = 0
        self._active = False
        self._recovered = False
        self._window_start: datetime | None = None
        self._notified_active = False
        self._notified_recovered = False

    def _prune(self, now: datetime) -> None:
        cutoff = now - timedelta(seconds=settings.review_path_outage_window_seconds)
        while self._failures and self._failures[0][0] < cutoff:
            self._failures.popleft()

    def _evaluate(self, now: datetime) -> None:
        self._prune(now)
        distinct = {key for _, key in self._failures}
        if not self._active and len(distinct) >= (
            settings.review_path_outage_distinct_tasks
        ):
            self._active = True
            self._recovered = False
            self._window_start = self._failures[0][0]
            self._notified_active = False
            self._notified_recovered = False
        elif self._active and not self._failures:
            self._clear()

    def _clear(self) -> None:
        self._active = False
        self._recovered = True
        self._consecutive_ok = 0

    def record_failure(self, task_key: str, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        self._consecutive_ok = 0
        self._failures.append((now, str(task_key)))
        self._evaluate(now)

    def record_success(self, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        if not self._active:
            return
        self._consecutive_ok += 1
        if self._consecutive_ok >= settings.review_path_recovery_successes:
            self._failures.clear()
            self._clear()

    def state(self, now: datetime | None = None) -> dict[str, Any]:
        """The panel contract: outage_active, type, window_start, recovered."""
        self._evaluate(now or datetime.now(UTC))
        return {
            "outage_active": self._active,
            "type": OUTAGE_TYPE,
            "window_start": self._window_start,
            "recovered": self._recovered,
        }

    def pending_transition(self) -> str | None:
        """'active' / 'recovered' once per transition, else None (dedup)."""
        self._evaluate(datetime.now(UTC))
        if self._active and not self._notified_active:
            self._notified_active = True
            return "active"
        if self._recovered and self._notified_active and not self._notified_recovered:
            self._notified_recovered = True
            return "recovered"
        return None


_tracker = ReviewPathHealthTracker()


def get_review_path_tracker() -> ReviewPathHealthTracker:
    return _tracker


def record_review_path_failure(task_key: Any) -> None:
    _tracker.record_failure(str(task_key))


def record_review_path_success() -> None:
    _tracker.record_success()


async def sweep_review_path_health() -> None:
    """Emit the (single) outage / recovery notification for a transition."""
    transition = _tracker.pending_transition()
    if transition is None:
        return
    state = _tracker.state()
    from roboco.services.notification import NotificationService

    try:
        await NotificationService().send_review_path_outage_notification(
            active=transition == "active",
            window_start=state["window_start"],
        )
    except Exception as exc:
        logger.warning("Failed to send review-path outage notification", error=str(exc))
