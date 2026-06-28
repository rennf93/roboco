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

import logging
from typing import TYPE_CHECKING

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

# Redis mutex guarding the ~40min release execute against concurrent
# approves; TTL backstops a crash, lock is released on completion.
_RELEASE_LOCK_PREFIX = "roboco:release_proposal:"
_RELEASE_LOCK_TTL_SECONDS = 3000  # 50 min > 40 min CI ceiling


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

        F013: a Redis ``SET NX`` mutex keyed by the proposal id guards the
        ~40min execute against concurrent approves (double-click / panel retry)
        that would race on the shared, ``rm -rf``'d writable release clone. A
        second approve while the lock is held returns ``already_in_progress``
        without running the executor. Fail-closed on Redis outage — a release is
        rare and CEO-gated, and the race it prevents corrupts the release.
        """
        task = await get_task_service(self.session).get(task_id)
        if task is None or task.source != RELEASE_MANAGER_SOURCE:
            return None
        report_dict = markers.get_release_report(task)
        if report_dict is None:
            return None

        lock_key = f"{_RELEASE_LOCK_PREFIX}{task_id}"
        lock = await self._acquire_release_lock(lock_key)
        if lock is None:
            report = report_from_dict(report_dict)
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

        try:
            report = report_from_dict(report_dict)
            executor = await get_release_executor(self.session)
            result = await executor.execute(report)
            if result.status == "published":
                task.status = TaskStatus.COMPLETED
                await self.session.flush()
            return result
        finally:
            await self._release_release_lock(lock_key)

    async def _acquire_release_lock(self, lock_key: str) -> bool | None:
        """``SET NX EX`` the release mutex. Returns True if acquired, None if held
        or Redis is unavailable (fail-closed → treat as held)."""
        try:
            conn = redis.from_url(settings.redis_url)
            try:
                acquired = await conn.set(
                    lock_key, "release", nx=True, ex=_RELEASE_LOCK_TTL_SECONDS
                )
                # redis-py returns True on SET NX success, None on conflict.
                return True if acquired else None
            finally:
                await conn.aclose()
        except Exception as exc:
            logger.warning("release lock acquire failed (redis): %s", exc)
            return None

    async def _release_release_lock(self, lock_key: str) -> None:
        """Best-effort ``DEL`` the release mutex (the TTL is the backstop)."""
        try:
            conn = redis.from_url(settings.redis_url)
            try:
                await conn.delete(lock_key)
            finally:
                await conn.aclose()
        except Exception as exc:
            logger.warning("release lock release failed (redis): %s", exc)

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
