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

from typing import TYPE_CHECKING

from roboco.foundation.policy.content import markers
from roboco.models.base import TaskStatus
from roboco.services.base import BaseService
from roboco.services.release_executor import get_release_executor
from roboco.services.release_readiness import report_from_dict
from roboco.services.task import RELEASE_MANAGER_SOURCE, get_task_service

if TYPE_CHECKING:
    from uuid import UUID

    from sqlalchemy.ext.asyncio import AsyncSession

    from roboco.db.tables import TaskTable
    from roboco.services.release_executor import ReleaseResult


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
        """
        task = await get_task_service(self.session).get(task_id)
        if task is None or task.source != RELEASE_MANAGER_SOURCE:
            return None
        report_dict = markers.get_release_report(task)
        if report_dict is None:
            return None
        report = report_from_dict(report_dict)
        executor = await get_release_executor(self.session)
        result = await executor.execute(report)
        if result.status == "published":
            task.status = TaskStatus.COMPLETED
            await self.session.flush()
        return result

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
