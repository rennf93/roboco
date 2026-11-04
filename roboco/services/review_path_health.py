"""Cross-task outage detection for the review path (evidence + git reads).

Extends the gateway-health recovery subsystem: the orchestrator's reaper tick
calls ``sweep_review_path_health`` beside ``_maybe_recover_broken_gateway``.
Failures of the evidence / git-readonly pipeline are recorded per task key; an
outage is declared only when failures span at least
``review_path_outage_distinct_tasks`` DISTINCT tasks inside the rolling
``review_path_outage_window_seconds`` window, so one task failing repeatedly is
never an outage. One notification is emitted per outage window (on trip) and
one on recovery. Detection only - no auto-remediation.

State (failure window, active/recovered/window_start, transition queue) lives
in Redis, so the api role (where agents' failures land), the dispatcher (where
the sweep runs) and whichever process serves the panel read all share one
tracker. Every mutation is atomic (HSETNX / HDEL decide the single winner of a
transition, LPOP drains the queue), so each transition is delivered exactly
once even with concurrent sweeps. Redis errors are logged and swallowed:
tracking never fails a read.

The client is ``redis.asyncio`` with 1s socket timeouts, so a slow or dead
Redis never blocks the event loop.
"""

from __future__ import annotations

import contextlib
import functools
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import redis.asyncio as redis

from roboco.config import settings
from roboco.logging import get_logger
from roboco.services.base import NotFoundError, UnauthorizedError, ValidationError

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

OUTAGE_TYPE = "review_path_tools"
_TASK_KEY_LEN = 8
_TASK_PREFIX_RE = re.compile(rf"^[0-9a-f]{{{_TASK_KEY_LEN}}}$")
_FAILURES_KEY = "roboco:review_path:failures"
_STATE_KEY = "roboco:review_path:state"
_TRANSITIONS_KEY = "roboco:review_path:transitions"
_REDIS_TIMEOUT_SECONDS = 1


def _fail_open(default: Any) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Log and swallow any Redis error, returning ``default``."""

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return await fn(*args, **kwargs)
            except Exception as exc:
                logger.warning(
                    "review_path_health_redis_error", op=fn.__name__, error=str(exc)
                )
                return default

        return wrapper

    return deco


def _ts(value: datetime) -> float:
    return value.timestamp()


def _dt(value: float | str) -> datetime:
    return datetime.fromtimestamp(float(value), UTC)


class ReviewPathHealthTracker:
    """Rolling-window failure tracker with a deduplicated outage state,
    persisted in Redis so every role shares it."""

    def __init__(self, client: Any = None) -> None:
        self._client = client

    @property
    def _r(self) -> Any:
        if self._client is None:
            self._client = redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_timeout=_REDIS_TIMEOUT_SECONDS,
                socket_connect_timeout=_REDIS_TIMEOUT_SECONDS,
            )
        return self._client

    async def _window(self, now: datetime) -> list[tuple[str, float]]:
        cutoff = _ts(now) - settings.review_path_outage_window_seconds
        await self._r.zremrangebyscore(_FAILURES_KEY, "-inf", f"({cutoff}")
        window: list[tuple[str, float]] = await self._r.zrange(
            _FAILURES_KEY, 0, -1, withscores=True
        )
        return window

    async def _evaluate(self, now: datetime) -> None:
        window = await self._window(now)
        active = await self._r.hget(_STATE_KEY, "active") == "1"
        distinct = {member.rsplit("|", 1)[0] for member, _ in window}
        if not active and len(distinct) >= settings.review_path_outage_distinct_tasks:
            await self._trip(window[0][1])
        elif active and not window:
            await self._expire()

    async def _trip(self, window_start: float) -> None:
        if await self._r.hsetnx(_STATE_KEY, "active", "1"):
            await self._r.hset(
                _STATE_KEY,
                mapping={
                    "recovered": "0",
                    "ok": "0",
                    "window_start": repr(window_start),
                },
            )
            await self._r.rpush(_TRANSITIONS_KEY, "active")

    async def _expire(self) -> None:
        """The window emptied with no read having succeeded: close the outage
        without claiming recovery (``recovered`` stays False)."""
        if await self._r.hdel(_STATE_KEY, "active"):
            await self._r.hset(_STATE_KEY, "ok", "0")
            await self._r.rpush(_TRANSITIONS_KEY, "expired")

    async def _clear(self) -> None:
        if await self._r.hdel(_STATE_KEY, "active"):
            await self._r.hset(_STATE_KEY, mapping={"recovered": "1", "ok": "0"})
            await self._r.rpush(_TRANSITIONS_KEY, "recovered")

    @_fail_open(None)
    async def record_failure(self, task_key: str, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC)
        await self._r.hset(_STATE_KEY, "ok", "0")
        await self._r.zadd(_FAILURES_KEY, {f"{task_key}|{uuid4().hex}": _ts(now)})
        await self._evaluate(now)

    @_fail_open(None)
    async def record_success(self) -> None:
        if await self._r.hget(_STATE_KEY, "active") != "1":
            return
        ok = await self._r.hincrby(_STATE_KEY, "ok", 1)
        if ok >= settings.review_path_recovery_successes:
            await self._r.delete(_FAILURES_KEY)
            await self._clear()

    async def state(self, now: datetime | None = None) -> dict[str, Any]:
        """The panel contract: outage_active, type, window_start, recovered."""
        snapshot: dict[str, str] = await self._snapshot(now or datetime.now(UTC))
        window_start = snapshot.get("window_start")
        return {
            "outage_active": snapshot.get("active") == "1",
            "type": OUTAGE_TYPE,
            "window_start": _dt(window_start) if window_start else None,
            "recovered": snapshot.get("recovered") == "1",
        }

    @_fail_open({})
    async def _snapshot(self, now: datetime) -> dict[str, str]:
        await self._evaluate(now)
        snapshot: dict[str, str] = await self._r.hgetall(_STATE_KEY)
        return snapshot

    @_fail_open(None)
    async def pending_transition(self) -> str | None:
        """Next unreported 'active' / 'recovered' / 'expired' transition.

        Each transition is queued exactly once when it happens, and LPOP hands
        it to exactly one caller, so an outage that trips and clears between
        sweeps still yields active then recovered, and concurrent sweeps never
        double-send.
        """
        await self._evaluate(datetime.now(UTC))
        transition: str | None = await self._r.lpop(_TRANSITIONS_KEY)
        return transition


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
    except Exception as exc:
        logger.warning(
            "review_path_key_active_task_lookup_failed",
            agent_id=str(agent_id),
            error=str(exc),
        )
        task = None
    if task is not None:
        return str(task.id)[:_TASK_KEY_LEN]
    return f"agent:{agent_id}"


async def record_review_path_failure(task_key: Any) -> None:
    """Record a failure; a full task UUID collapses to its 8-char prefix so the
    evidence legs and the git routes (which may only know the branch) agree."""
    key = str(task_key)
    with contextlib.suppress(ValueError):
        key = str(UUID(key))[:8]
    await _tracker.record_failure(key)


async def record_review_path_success() -> None:
    await _tracker.record_success()


async def sweep_review_path_health() -> None:
    """Emit one outage / recovery notification per unreported transition."""
    from roboco.services.notification import NotificationService

    while (transition := await _tracker.pending_transition()) is not None:
        try:
            await NotificationService().send_review_path_outage_notification(
                active=transition == "active",
                recovered=transition == "recovered",
                window_start=(await _tracker.state())["window_start"],
            )
        except Exception as exc:
            logger.warning(
                "Failed to send review-path outage notification", error=str(exc)
            )
