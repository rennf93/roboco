"""Cross-task outage detection for the review path (evidence + git reads).

Extends the gateway-health recovery subsystem: the orchestrator's reaper tick
calls ``sweep_review_path_health`` beside ``_maybe_recover_broken_gateway``.
Failures of the evidence / git-readonly pipeline are recorded per task key; an
outage is declared only when failures span at least
``review_path_outage_distinct_tasks`` DISTINCT tasks inside the rolling
``review_path_outage_window_seconds`` window, so one task failing repeatedly is
never an outage. One notification is emitted per outage window (on trip) and
one on recovery. Detection only - no auto-remediation.

ponytail: in-memory per process. Failures and the sweep live in the dispatcher
(agents' API URL), so nginx sends /api/system-health/ there; the api role's
tracker is always empty. A restart forgets the window, which at worst delays a
trip by one window. Move to Redis if a second recording process ever appears.
"""

from __future__ import annotations

import contextlib
import re
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from roboco.config import settings
from roboco.logging import get_logger
from roboco.services.base import NotFoundError, UnauthorizedError, ValidationError

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

OUTAGE_TYPE = "review_path_tools"
_TASK_KEY_LEN = 8
_TASK_PREFIX_RE = re.compile(rf"^[0-9a-f]{{{_TASK_KEY_LEN}}}$")


class ReviewPathHealthTracker:
    """Rolling-window failure tracker with a deduplicated outage state."""

    def __init__(self) -> None:
        self._failures: deque[tuple[datetime, str]] = deque()
        self._consecutive_ok = 0
        self._active = False
        self._recovered = False
        self._window_start: datetime | None = None
        self._unreported: deque[str] = deque()

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
            self._unreported.append("active")
        elif self._active and not self._failures:
            self._expire()

    def _expire(self) -> None:
        """The window emptied with no read having succeeded: close the outage
        without claiming recovery (``recovered`` stays False)."""
        self._active = False
        self._consecutive_ok = 0
        self._unreported.append("expired")

    def _clear(self) -> None:
        self._active = False
        self._recovered = True
        self._consecutive_ok = 0
        self._unreported.append("recovered")

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
        """Next unreported 'active' / 'recovered' transition, else None.

        Each transition is queued exactly once when it happens, so an outage
        that trips and clears between sweeps still yields active then recovered.
        """
        self._evaluate(datetime.now(UTC))
        return self._unreported.popleft() if self._unreported else None


_tracker = ReviewPathHealthTracker()


def get_review_path_tracker() -> ReviewPathHealthTracker:
    return _tracker


_CALLER_ERRORS = (NotFoundError, ValidationError, UnauthorizedError)


def is_infrastructure_failure(exc: BaseException) -> bool:
    """True when ``exc`` signals a platform fault, not a caller error.

    A NotFound/Validation/Unauthorized error (e.g. a deleted branch), including
    one wrapped as another error's cause, is a 4xx-class outcome and never an
    outage signal; timeouts, git command failures and OSError are.
    """
    seen: set[int] = set()
    cur: BaseException | None = exc
    while cur is not None and id(cur) not in seen:
        if isinstance(cur, _CALLER_ERRORS):
            return False
        seen.add(id(cur))
        cur = cur.__cause__ or cur.__context__
    return True


def review_path_key(
    *, task_id: Any = None, branch: str | None = None, project_slug: str = ""
) -> str:
    """One canonical outage-tracker key per read: the task's 8-char id prefix.

    A task id (full UUID) and the id embedded in its branch name
    (``.../<root>--<sub>``, last segment) normalize to the same key, so one task
    failing across status/log/diff counts once. Only when no task applies does
    it fall back to a namespaced branch or project key.
    """
    if task_id:
        return str(task_id)[:_TASK_KEY_LEN]
    if branch:
        tail = branch.rsplit("/", 1)[-1].rsplit("--", 1)[-1]
        if _TASK_PREFIX_RE.match(tail):
            return tail
        return f"branch:{branch}"
    return f"project:{project_slug}"


async def resolve_review_path_key(
    db: AsyncSession,
    agent_id: UUID,
    *,
    task_id: Any = None,
    branch: str | None = None,
    project_slug: str = "",
) -> str:
    """``review_path_key``, but a read naming no task/branch is keyed on the
    caller's server-side identity: its active task, else ``agent:<id>``.

    Without this every task-less read collapses to one ``project:<slug>`` key
    and a cross-task outage can never reach the distinct-task threshold.
    """
    if task_id or branch:
        return review_path_key(
            task_id=task_id, branch=branch, project_slug=project_slug
        )
    try:
        from roboco.services.task import get_task_service

        task = await get_task_service(db).get_active_task_for_agent(agent_id)
    except Exception:
        task = None
    if task is not None:
        return str(task.id)[:_TASK_KEY_LEN]
    return f"agent:{agent_id}"


def record_review_path_failure(task_key: Any) -> None:
    """Record a failure; a full task UUID collapses to its 8-char prefix so the
    evidence legs and the git routes (which may only know the branch) agree."""
    key = str(task_key)
    with contextlib.suppress(ValueError):
        key = str(UUID(key))[:8]
    _tracker.record_failure(key)


def record_review_path_success() -> None:
    _tracker.record_success()


async def sweep_review_path_health() -> None:
    """Emit one outage / recovery notification per unreported transition."""
    from roboco.services.notification import NotificationService

    while (transition := _tracker.pending_transition()) is not None:
        try:
            await NotificationService().send_review_path_outage_notification(
                active=transition == "active",
                recovered=transition == "recovered",
                window_start=_tracker.state()["window_start"],
            )
        except Exception as exc:
            logger.warning(
                "Failed to send review-path outage notification", error=str(exc)
            )
