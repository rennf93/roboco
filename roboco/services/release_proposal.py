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

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import TaskTable

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
        try:
            executor = await get_release_executor(self.session)
            heartbeat_task = asyncio.create_task(
                self._heartbeat_loop(lock_key, lock_token)
            )
            result = await executor.execute(report)
            if result.status == "published":
                task.status = TaskStatus.COMPLETED
                await self.session.flush()
            return result
        finally:
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
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
            conn = redis.from_url(settings.redis_url)
            try:
                acquired = await conn.set(
                    lock_key, token, nx=True, ex=_RELEASE_LOCK_TTL_SECONDS
                )
                # redis-py returns True on SET NX success, None on conflict.
                return token if acquired else None
            finally:
                await conn.aclose()
        except Exception as exc:
            logger.warning("release lock acquire failed (redis): %s", exc)
            raise ReleaseLockUnavailable(str(exc)) from exc

    async def _release_release_lock(self, lock_key: str, token: str) -> None:
        """Compare-and-del the release mutex (only if we still own it)."""
        try:
            conn = redis.from_url(settings.redis_url)
            try:
                await conn.eval(_RELEASE_LOCK_RELEASE_SCRIPT, 1, lock_key, token)
            finally:
                await conn.aclose()
        except Exception as exc:
            logger.warning("release lock release failed (redis): %s", exc)

    async def _heartbeat_release_lock(self, lock_key: str, token: str) -> bool:
        """Compare-and-expire the release mutex. True if we still own it."""
        conn = redis.from_url(settings.redis_url)
        try:
            res = await conn.eval(
                _RELEASE_LOCK_HEARTBEAT_SCRIPT,
                1,
                lock_key,
                token,
                _RELEASE_LOCK_TTL_SECONDS,
            )
            return bool(res)
        finally:
            await conn.aclose()

    async def _heartbeat_loop(self, lock_key: str, token: str) -> None:
        """Refresh the lock TTL while the execute owns it.

        Refreshes before the first sleep so a fast execute still extends the
        TTL. A refresh error logs and continues (never crashes the execute); if
        the lock is no longer ours (returned 0) we stop — the TTL backstop and
        the fencing token still hold the line.
        """
        while True:
            try:
                if not await self._heartbeat_release_lock(lock_key, token):
                    logger.critical(
                        "release lock no longer owned during execute — "
                        "TTL backstop active; a concurrent approve was refused "
                        "by the fencing token"
                    )
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
