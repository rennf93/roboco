"""Release-proposal service — the CEO's approve / reject glue over a held proposal.

The release-manager engine opens a HELD proposal task (``release_manager``
source). This service is what the CEO-gated routes call: it finds the open
proposal, and on approval runs the fail-closed ``ReleaseExecutor`` over the
stored readiness report (marking the proposal completed only when the release
actually publishes); on rejection it records the CEO's required changes and
keeps the proposal held for revision. It never publishes on its own — the
executor is fail-closed and the proposal stays open unless a publish succeeds.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from uuid import uuid4

import redis.asyncio as redis

from roboco.config import settings
from roboco.foundation.policy.content import markers
from roboco.models.base import TaskStatus
from roboco.services.base import BaseService
from roboco.services.release_executor import ReleaseResult, get_release_executor
from roboco.services.release_readiness import report_from_dict
from roboco.services.task import RELEASE_MANAGER_SOURCE, get_task_service

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from roboco.db.tables import TaskTable
    from roboco.services.release_readiness import ReleaseReadinessReport

logger = logging.getLogger(__name__)


class ReleaseLockUnavailable(Exception):
    """Redis is unreachable so the release mutex can't be acquired.

    Distinct from "the lock is held": a held lock is a concurrent approve (retry
    later); an unavailable Redis is an infrastructure failure (fix Redis, then
    retry). Both stay fail-closed — the execute never runs without the mutex.
    """


# Redis mutex guarding the ~40min release execute against concurrent
# approves. The TTL only backstops a crash; a background heartbeat refreshes
# it while the execute owns the lock, and a fencing token makes the release
# compare-and-del so a late first-finally can't delete a usurper's lock.
_RELEASE_LOCK_PREFIX = "roboco:release_proposal:"
_RELEASE_LOCK_TTL_SECONDS = 3000  # 50 min > 40 min CI ceiling; crash backstop
_RELEASE_LOCK_HEARTBEAT_SECONDS = 60.0
# Only delete/extend the lock when its value still equals our fencing token.
_RELEASE_LOCK_RELEASE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""
_RELEASE_LOCK_HEARTBEAT_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("expire", KEYS[1], ARGV[2])
else
    return 0
end
"""


class ReleaseProposalService(BaseService):
    """Find / approve / reject the single open release proposal."""

    service_name = "release_proposal"

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)
        # One shared redis client per approve, closed once in approve()'s finally
        # (replaces a from_url pool per heartbeat tick — ~40 pools per release).
        self._redis: redis.Redis | None = None

    async def _redis_conn(self) -> redis.Redis:
        if self._redis is None:
            self._redis = redis.from_url(settings.redis_url)
        return self._redis

    async def _close_redis(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def open_proposal(self) -> TaskTable | None:
        """The single held release proposal awaiting the CEO, or None."""
        proposals = await get_task_service(self.session).list_open_release_proposals()
        return proposals[0] if proposals else None

    async def approve(self, task_id: UUID) -> ReleaseResult | None:
        """Run the fail-closed executor over the proposal's stored report.

        Returns the executor result, or None when the task is not a release
        proposal / has no stored report. The proposal is marked COMPLETED only
        when the release actually publishes — a gate/CI failure leaves it open so
        the CEO can retry after the cause is fixed.

        A Redis ``SET NX`` mutex keyed by the proposal id guards the execute
        against concurrent approves (double-click / panel retry) that would race
        on the shared, ``rm -rf``'d writable release clone. The lock value is a
        fencing token; a background heartbeat refreshes the TTL while the
        execute owns it, and the release is a compare-and-del — so a second
        approve can't acquire mid-execute (TTL never expires while it's alive)
        and a late first-finally can't delete a usurper's lock. A second approve
        while the lock is held returns ``already_in_progress`` without running
        the executor. Fail-closed on Redis outage — a release is rare and
        CEO-gated, and the race it prevents corrupts the release.
        """
        task = await get_task_service(self.session).get(task_id)
        if task is None or task.source != RELEASE_MANAGER_SOURCE:
            return None
        report_dict = markers.get_release_report(task)
        if report_dict is None:
            return None

        lock_key = f"{_RELEASE_LOCK_PREFIX}{task_id}"
        report = report_from_dict(report_dict)
        try:
            lock_token = await self._acquire_release_lock(lock_key)
        except ReleaseLockUnavailable as exc:
            # Fail-closed: the execute never runs without the mutex. But a Redis
            # outage is NOT a concurrent approve — surface the real cause so the
            # CEO fixes Redis instead of waiting on a phantom second approve.
            logger.error("release lock unavailable (redis down): %s", exc)
            return ReleaseResult(
                status="redis_unavailable",
                version=report.proposed_version,
                files_changed=[],
                commit_sha=None,
                release_url=None,
                detail=(
                    "Redis is unavailable so the release mutex can't be acquired"
                    " (fail-closed: the execute did not run). Restore Redis and"
                    " retry — this is not a concurrent-approve conflict."
                ),
            )
        if lock_token is None:
            return ReleaseResult(
                status="already_in_progress",
                version=report.proposed_version,
                files_changed=[],
                commit_sha=None,
                release_url=None,
                detail=(
                    "A release execute is already in progress for this proposal "
                    "(concurrent approve refused). Wait for it to finish and retry."
                ),
            )

        heartbeat_task: asyncio.Task[None] | None = None
        execute_task: asyncio.Task[ReleaseResult] | None = None
        # Set by the heartbeat when IT cancels execute on lock-loss, so the
        # CancelledError handler below can distinguish a lock-loss abort (→
        # structured ``lock_lost`` result) from an external cancellation of the
        # approve coroutine itself (→ must propagate).
        lock_lost = asyncio.Event()
        try:
            executor = await get_release_executor(self.session)
            execute_task = asyncio.create_task(executor.execute(report))
            heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(lock_key, lock_token, execute_task, lock_lost)
            )
            try:
                result = await execute_task
            except asyncio.CancelledError:
                if not lock_lost.is_set():
                    # External cancellation of approve itself — propagate, do not
                    # mask it as a lock-loss.
                    raise
                logger.critical(
                    "release execute aborted: lock lost mid-execute (fail-closed)"
                )
                return ReleaseResult(
                    status="lock_lost",
                    version=report.proposed_version,
                    files_changed=[],
                    commit_sha=None,
                    release_url=None,
                    detail=(
                        "The release lock was lost mid-execute (an extended Redis"
                        " outage let the mutex TTL expire); the execute was"
                        " aborted fail-closed so a concurrent approve could not"
                        " rm -rf the in-flight release clone. Retry the approve."
                    ),
                )
            # Close the proposal when the release actually shipped — including a
            # retry that finds the tag already published (a prior publish whose
            # route commit/HTTP 504'd left the proposal non-terminal). The old
            # `== "published"`-only check wedged already_published open forever.
            if result.status in ("published", "already_published"):
                task.status = TaskStatus.COMPLETED
                await self.session.flush()
                await self._draft_x_post(report)
                await self._draft_video(report)
            return result
        finally:
            await self._finalize_release_lock(
                heartbeat_task, execute_task, lock_key, lock_token
            )
            await self._close_redis()

    async def _draft_x_post(self, report: ReleaseReadinessReport) -> None:
        """Hand the just-published release to the X engine for a held
        announcement draft (best-effort — never raises into approve(); a
        drafting failure must not affect the release's already-succeeded
        publish). Off/no-creds is itself a no-op inside the engine."""
        try:
            from roboco.services.x_engine import get_x_engine

            await get_x_engine(self.session).draft_release_post(
                version=report.proposed_version,
                highlights=list(report.change_summary),
            )
        except Exception as exc:
            logger.warning("x-post draft failed (best-effort): %s", exc)

    async def _draft_video(self, report: ReleaseReadinessReport) -> None:
        """Hand the just-published release to the video engine for a held
        UX/UI authoring task (best-effort — never raises into approve(); a
        drafting failure must not affect the release's already-succeeded
        publish). Off/no-sub-switch is itself a no-op inside the engine."""
        try:
            from roboco.services.video_engine import get_video_engine

            await get_video_engine(self.session).draft_release_video(
                version=report.proposed_version,
                changelog=report.drafted_changelog,
            )
        except Exception as exc:
            logger.warning("video draft failed (best-effort): %s", exc)

    async def _finalize_release_lock(
        self,
        heartbeat_task: asyncio.Task[None] | None,
        execute_task: asyncio.Task[ReleaseResult] | None,
        lock_key: str,
        lock_token: str,
    ) -> None:
        """Cancel the heartbeat/execute tasks and release the mutex (best-effort)."""
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
        if execute_task is not None and not execute_task.done():
            execute_task.cancel()
            await asyncio.gather(execute_task, return_exceptions=True)
        await self._release_release_lock(lock_key, lock_token)

    async def _acquire_release_lock(self, lock_key: str) -> str | None:
        """``SET NX EX`` the release mutex with a fencing-token value.

        Returns the token if acquired, None if held (a concurrent approve owns
        it). Raises :class:`ReleaseLockUnavailable` if Redis itself is
        unreachable so the caller can distinguish an infra failure from a
        concurrent-approve conflict (both stay fail-closed).
        """
        token = uuid4().hex
        try:
            conn = await self._redis_conn()
            acquired = await conn.set(
                lock_key, token, nx=True, ex=_RELEASE_LOCK_TTL_SECONDS
            )
            # redis-py returns True on SET NX success, None on conflict.
            return token if acquired else None
        except Exception as exc:
            logger.warning("release lock acquire failed (redis): %s", exc)
            raise ReleaseLockUnavailable(str(exc)) from exc

    async def _release_release_lock(self, lock_key: str, token: str) -> None:
        """Compare-and-del the release mutex (only if we still own it)."""
        try:
            conn = await self._redis_conn()
            await conn.eval(_RELEASE_LOCK_RELEASE_SCRIPT, 1, lock_key, token)
        except Exception as exc:
            logger.warning("release lock release failed (redis): %s", exc)

    async def _heartbeat_release_lock(self, lock_key: str, token: str) -> bool:
        """Compare-and-expire the release mutex. True if we still own it."""
        conn = await self._redis_conn()
        res = await conn.eval(
            _RELEASE_LOCK_HEARTBEAT_SCRIPT,
            1,
            lock_key,
            token,
            _RELEASE_LOCK_TTL_SECONDS,
        )
        return bool(res)

    async def _heartbeat_loop(
        self,
        lock_key: str,
        token: str,
        execute_task: asyncio.Task[ReleaseResult],
        lock_lost: asyncio.Event,
    ) -> None:
        """Refresh the lock TTL while the execute owns it.

        Refreshes before the first sleep so a fast execute still extends the
        TTL. A refresh error logs and continues (never crashes the execute); if
        the lock is no longer ours (returned 0 — only reachable after a >TTL
        Redis outage lets the mutex expire mid-execute) we CANCEL the in-flight
        execute fail-closed rather than ``return`` silently and leave it running
        unguarded — otherwise a concurrent approve (once Redis returns) can
        acquire the lock and ``_prepare_release_clone`` ``rm -rf``'s the shared
        release clone while the first execute is still mid-``run_gate``. The
        fencing token still prevents the first finally from deleting the
        usurper's lock; this prevents the usurper's rm -rf from corrupting the
        first execute.
        """
        while True:
            try:
                if not await self._heartbeat_release_lock(lock_key, token):
                    logger.critical(
                        "release lock no longer owned during execute — "
                        "aborting execute fail-closed so a concurrent approve"
                        " cannot rm -rf the in-flight release clone"
                    )
                    lock_lost.set()
                    execute_task.cancel()
                    return
            except Exception as exc:
                logger.warning("release lock heartbeat failed (redis): %s", exc)
            await asyncio.sleep(_RELEASE_LOCK_HEARTBEAT_SECONDS)

    async def reject(self, task_id: UUID, required_changes: str) -> TaskTable | None:
        """Record the CEO's required changes; keep the proposal held for revision."""
        task = await get_task_service(self.session).get(task_id)
        if task is None or task.source != RELEASE_MANAGER_SOURCE:
            return None
        markers.set_release_required_changes(task, required_changes)
        await self.session.flush()
        return task


def get_release_proposal_service(session: AsyncSession) -> ReleaseProposalService:
    """Construct a ReleaseProposalService bound to ``session``."""
    return ReleaseProposalService(session)


# In-flight background approves keyed by proposal task id. The HTTP approve
# route dispatches the ~40min execute asynchronously (a synchronous request
# would 504 at any proxy before the fail-closed gate/CI/publish finished) and
# returns 202 immediately; the panel polls GET /proposal for the final status.
# This registry lets a status endpoint / tests await the dispatched execute;
# it self-cleans via a done-callback and the Redis mutex still prevents a
# double-execute on a second click.
_INFLIGHT_APPROVES: dict[UUID, asyncio.Task[None]] = {}


async def sweep_orphan_release_locks() -> None:
    """Delete release-proposal mutex keys whose owners aren't in flight.

    A restart mid-execute kills ``_run_approve_background`` but the Redis mutex
    (TTL 3000s) persists with no heartbeat, so a CEO retry gets
    ``already_in_progress`` for up to 50 min. Called from ``Orchestrator.start``
    before the release-manager loop launches — after a restart the in-flight
    registry is empty, so every surviving key is an orphan. Best-effort: a Redis
    failure logs a warning and does not raise (a down Redis at startup must not
    crash the orchestrator).
    """
    from uuid import UUID

    try:
        conn = redis.from_url(settings.redis_url)
        try:
            keys = await conn.keys(f"{_RELEASE_LOCK_PREFIX}*")
            for key in keys or []:
                task_id = (
                    key.decode() if isinstance(key, bytes) else str(key)
                ).removeprefix(_RELEASE_LOCK_PREFIX)
                try:
                    uid = UUID(task_id)
                except ValueError:
                    continue
                if uid not in _INFLIGHT_APPROVES:
                    await conn.delete(key)
        finally:
            await conn.aclose()
    except Exception as exc:
        logger.warning("release lock orphan sweep failed (redis): %s", exc)


async def _run_approve_background(
    task_id: UUID, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Run ``approve`` in a background task with a fresh session (the request
    session closes when the 202 returns). Commits the outcome; a failure logs
    and rolls back — the proposal stays open for the CEO to retry."""
    async with session_factory() as bg_db:
        try:
            result = await get_release_proposal_service(bg_db).approve(task_id)
            logger.info(
                "release approve completed task_id=%s status=%s",
                task_id,
                result.status if result is not None else "no_report",
            )
            await bg_db.commit()
        except Exception:
            logger.exception(
                "release approve background task failed task_id=%s", task_id
            )
            await bg_db.rollback()


def dispatch_approve(
    task_id: UUID, session_factory: async_sessionmaker[AsyncSession]
) -> asyncio.Task[None]:
    """Spawn the long release execute in a background task so the HTTP approve
    route can return 202 immediately. Registered in ``_INFLIGHT_APPROVES`` for
    observability (done-callback removes the entry)."""
    bg_task = asyncio.create_task(_run_approve_background(task_id, session_factory))
    _INFLIGHT_APPROVES[task_id] = bg_task
    bg_task.add_done_callback(lambda _t: _INFLIGHT_APPROVES.pop(task_id, None))
    return bg_task
